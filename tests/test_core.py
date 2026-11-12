import json

import numpy as np
import pytest
from scipy.spatial import cKDTree

from shaker import seeds
from shaker.builder import SystemSpec, build_system
from shaker.gro import read_gro
from shaker.lipids.templates import MIN_SEPARATION, min_separation
from shaker.peptide import build_oligomer, prepare_peptide
from shaker.peptide.helix import internal_prolines
from shaker.peptide.martinize import expected_charge
from shaker.peptide.oligomer import (
    MIN_BEAD_DISTANCE,
    helix_axis,
    hydrophobic_direction,
)

from conftest import MAG2


# seeds ------------------------------------------------------------------------------------

def test_replica_seed_is_stable_and_distinct():
    assert seeds.replica_seed(1, "c", 0) == seeds.replica_seed(1, "c", 0)
    values = {seeds.replica_seed(1, "c", i) for i in range(200)}
    assert len(values) == 200
    assert seeds.replica_seed(1, "c", 0) != seeds.replica_seed(2, "c", 0)
    assert seeds.replica_seed(1, "c", 0) != seeds.replica_seed(1, "d", 0)


def test_gromacs_seed_fits_32_bits():
    for i in range(100):
        assert 0 < seeds.gromacs_seed(seeds.replica_seed(7, "x", i)) < 2**31


# lipid library ----------------------------------------------------------------------------

def test_library_templates_are_valid(library):
    assert len(library) == 113
    assert library.validate() == []


def test_lipid_charges_and_heads(library):
    assert library.get("POPE").charge == 0
    assert library.get("POPG").charge == -1
    assert library.get("POPC").head == "PC"
    assert min_separation(library.get("POPG").template) >= MIN_SEPARATION


def test_unknown_lipid_message(library):
    with pytest.raises(KeyError, match="shaker lipids list"):
        library.get("XXXX")


# peptide ----------------------------------------------------------------------------------

def test_expected_charge():
    assert expected_charge(MAG2, "free") == 3
    assert expected_charge(MAG2, "amidated") == 4


def test_internal_prolines():
    assert internal_prolines("GIGAVLKVLTTGLPALISWIKRKRQQ") == [14]
    assert internal_prolines(MAG2) == []


def test_prepare_peptide(mag2):
    assert mag2.charge == 3
    assert mag2.n_beads == len(mag2.coords)
    assert sum(a.name == "BB" for a in mag2.molecule.atoms) == len(MAG2)
    assert np.allclose(mag2.coords.mean(axis=0), 0, atol=1e-6)


def test_amidation_adds_one_charge(tmp_path):
    peptide = prepare_peptide("MAG2", MAG2, tmp_path, c_terminus="amidated")
    assert peptide.charge == 4


def test_proline_warning(tmp_path):
    peptide = prepare_peptide("MEL", "GIGAVLKVLTTGLPALISWIKRKRQQ", tmp_path, c_terminus="amidated")
    assert any("proline" in w for w in peptide.warnings)


# oligomers --------------------------------------------------------------------------------

@pytest.mark.parametrize("size", [2, 4, 6])
@pytest.mark.parametrize("arrangement", ["parallel", "antiparallel"])
@pytest.mark.parametrize("face", ["inward", "outward"])
def test_oligomer_geometry(mag2, size, arrangement, face):
    atoms = mag2.molecule.atoms
    copies, distance = build_oligomer(mag2.coords, atoms, size, arrangement, face, 1.0)
    assert len(copies) == size
    assert distance >= 1.0

    # no overlap between copies
    for i in range(size):
        for j in range(i + 1, size):
            gap = np.linalg.norm(copies[i][:, None] - copies[j][None], axis=-1).min()
            assert gap >= MIN_BEAD_DISTANCE - 1e-9

    centre = np.concatenate(copies).mean(axis=0)
    axes = [helix_axis(c, atoms) for c in copies]
    for k, (copy, axis) in enumerate(zip(copies, axes)):
        # axes are parallel or antiparallel to the first copy
        expected = -1.0 if (arrangement == "antiparallel" and k % 2 == 1) else 1.0
        assert np.dot(axis, axes[0]) == pytest.approx(expected, abs=1e-6)
        # hydrophobic face points to or away from the ring centre
        to_centre = centre - copy.mean(axis=0)
        to_centre -= np.dot(to_centre, axis) * axis
        to_centre /= np.linalg.norm(to_centre)
        cosine = np.dot(hydrophobic_direction(copy, atoms, axis), to_centre)
        assert cosine == pytest.approx(1.0 if face == "inward" else -1.0, abs=1e-6)


# builder ----------------------------------------------------------------------------------

def _spec(mag2, **overrides):
    values = dict(
        lipids={"POPE": 24, "POPG": 8},
        water_beads_per_lipid=15,
        salt_molar=0.15,
        peptide=mag2,
        copies=4,
    )
    values.update(overrides)
    return SystemSpec(**values)


def test_build_counts_and_neutrality(mag2, tmp_path):
    result = build_system(_spec(mag2), seed=11, outdir=tmp_path)
    counts = dict(result.molecules)
    assert counts[mag2.name] == 4
    assert counts["POPE"] == 24 and counts["POPG"] == 8
    assert counts["W"] + counts["NA"] + counts["CL"] == 15 * 32
    assert result.net_charge == 0
    # solute charge is 4*(+3) + 8*(-1) = +4, so four more chloride than sodium
    assert counts["CL"] - counts["NA"] == 4

    resnrs, resnames, names, coords, box = read_gro(result.gro)
    expected_atoms = 4 * mag2.n_beads + 32 * 12 + 15 * 32
    assert len(coords) == expected_atoms
    assert names.count("W") == counts["W"]
    assert resnames.count("POPG") == 8 * 12

    manifest = json.loads(result.manifest.read_text())
    assert manifest["seed"] == 11
    assert manifest["total_beads"] == expected_atoms


def test_build_has_no_overlaps_across_periodic_boundaries(mag2, tmp_path):
    result = build_system(_spec(mag2), seed=3, outdir=tmp_path)
    resnrs, _, _, coords, box = read_gro(result.gro)
    wrapped = np.mod(coords, box)
    wrapped[wrapped >= box] = 0.0
    pairs = cKDTree(wrapped, boxsize=box).query_pairs(0.27, output_type="ndarray")
    resnrs = np.array(resnrs)
    different_residue = resnrs[pairs[:, 0]] != resnrs[pairs[:, 1]]
    # beads of one peptide span several residues, so compare molecules through residue gaps:
    # any close pair must belong to the same peptide chain (bonded neighbours)
    n_peptide = 4 * mag2.n_beads
    intermolecular = different_residue & ~(
        (pairs[:, 0] < n_peptide)
        & (pairs[:, 1] < n_peptide)
        & (pairs[:, 0] // mag2.n_beads == pairs[:, 1] // mag2.n_beads)
    )
    assert not intermolecular.any()


def test_same_seed_same_box_different_seed_different_box(mag2, tmp_path):
    a = build_system(_spec(mag2), seed=5, outdir=tmp_path / "a")
    b = build_system(_spec(mag2), seed=5, outdir=tmp_path / "b")
    c = build_system(_spec(mag2), seed=6, outdir=tmp_path / "c")
    assert a.gro.read_text() == b.gro.read_text()
    assert a.gro.read_text() != c.gro.read_text()


def test_oligomer_start_and_mixed_box(mag2, tmp_path):
    spec = _spec(mag2, copies=8, oligomer_size=4, oligomer_fraction=0.5)
    result = build_system(spec, seed=9, outdir=tmp_path)
    peptide = result.info["peptide"]
    assert peptide["oligomers"] == 1 and peptide["monomers"] == 4
    assert peptide["axis_distance_used"] >= 1.0
    assert result.net_charge == 0


def test_peptide_free_control(tmp_path):
    spec = SystemSpec(lipids={"POPE": 24, "POPG": 8})
    result = build_system(spec, seed=1, outdir=tmp_path)
    counts = dict(result.molecules)
    assert counts["NA"] - counts["CL"] == 8
    assert result.info["peptide"] is None


def test_invalid_spec_is_rejected(mag2, tmp_path):
    with pytest.raises(ValueError):
        build_system(SystemSpec(lipids={}), seed=1, outdir=tmp_path)
    with pytest.raises(ValueError):
        build_system(SystemSpec(lipids={"POPE": 8}, copies=2), seed=1, outdir=tmp_path)
