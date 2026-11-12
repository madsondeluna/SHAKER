# Good to know

Background on antimicrobial peptides (AMPs) and bacterial membranes, reduced to what changes a SHAKER input, an expected outcome or the reading of a result.

## Peptide properties that become SHAKER variables

| Property | Typical range in membrane-active AMPs | Where it enters SHAKER |
|---|---|---|
| Length | 10 to 50 residues | `sequence` |
| Net charge at pH 7 | +2 to +9 | computed in `prepare`, column of the `peptides` table |
| Hydrophobic residue fraction | 40% to 60% | `peptides` table |
| Hydrophobic moment (Eisenberg scale) | sequence dependent | `peptides` table, and the `hydrophobic_face` orientation of multimers |
| C-terminal amidation | common, adds +1 to the net charge | `c_terminus: amidated` |
| Proline or glycine hinge | melittin Pro14, maculatin 1.1 Pro15 | `prepare` warns, an ideal helix ignores the kink |

An alpha-helix rises 0.15 nm per residue. Spanning a hydrophobic core of 3 to 4 nm takes about 20 residues. A helical peptide shorter than that (aurein 1.2, 13 residues) cannot form a transmembrane pore alone, so `surface_bound` or `no_bilayer` are the expected outcomes for it.

Arginine binds phosphate groups more strongly than lysine, through bidentate hydrogen bonds from the guanidinium group. Tryptophan anchors at the glycerol region of the bilayer. Both effects are represented in Martini 3 only through bead types, without explicit hydrogen bonds.

## Membrane compositions

| Model | Composition in the literature | In the SHAKER library |
|---|---|---|
| Gram-negative inner membrane | POPE:POPG 3:1 or 7:3 | yes |
| Gram-negative inner membrane, ternary | POPE:POPG:cardiolipin 70:22:8 | no, cardiolipin is absent |
| Gram-positive membrane | POPG:POPE 7:3 | yes |
| Gram-positive membrane, with cardiolipin | POPG:cardiolipin 3:1 to 4:1 | no |
| Mammalian outer leaflet | POPC | yes |
| Mammalian outer leaflet, with sterol | POPC:cholesterol 7:3 | no, cholesterol was left out of the library |

Lipid properties with a testable consequence in self-assembly runs:

| Lipid | Charge | Property | Consequence |
|---|---|---|---|
| POPE | 0 | small head group, negative intrinsic curvature | raises the elastic cost of a toroidal pore, which needs positive curvature |
| POPG | -1 | anionic | attracts cationic peptides, expected to be enriched around them |
| POPC | 0 | cylindrical, no net charge | weaker electrostatic recruitment of cationic peptides |

The `copies` table records the lipids in contact with each peptide, which measures POPG enrichment directly.

Not represented in any SHAKER system: the outer membrane with lipopolysaccharide, peptidoglycan, lysyl-PG, sphingomyelin.

## Peptide to lipid ratio

Reorientation from the surface-aligned state to the inserted state is reported at a critical peptide to lipid molar ratio between 1:100 and 1:20. For a box of 128 lipids:

| Ratio | Peptide copies |
|---|---|
| 1:100 | 1 |
| 1:50 | 3 |
| 1:20 | 6 |
| 1:10 | 13 |

A scan across `copies` that brackets this range is the direct test of concentration dependence.

## Mechanisms and SHAKER outcome labels

| Mechanism | Structural signature | Outcome label |
|---|---|---|
| Barrel-stave | transmembrane peptide bundle, peptides line the water channel, lipid tails stay perpendicular to the membrane plane | `barrel_stave_pore` |
| Toroidal pore | both leaflets bend into the pore, lipid head groups and peptides line the channel | `toroidal_pore` |
| Carpet | peptides accumulate flat on the interface, bilayer intact or thinned | `surface_bound` |
| Detergent-like collapse | micelles or mixed aggregates in place of a bilayer | `no_bilayer` |
| Inserted without pore | transmembrane peptides, no continuous water channel | `transmembrane_no_pore` |

## Reference peptides

Helical peptides that fit the imposed-helix model. Net charge counts Lys and Arg as +1, Asp and Glu as -1, His as neutral, a free N-terminus as +1 and a free C-terminus as -1.

| Peptide | Sequence | Length | Net charge | Reported assembly | Reported mechanism | DOI |
|---|---|---|---|---|---|---|
| Magainin 2 | `GIGKFLHSAKKFGKAFVGEIMNS` | 23 | +3 | hexamer, trimer of antiparallel dimers | barrel-stave channel in POPE:POPG 3:1 | 10.1038/s41598-025-23539-1 |
| LL-37 | `LLGDFFRKSKEKIGKEFKRIVQRIKDFLRNLVPRTES` | 37 | +6 | tetramer of two antiparallel dimers | narrow channel in POPE:POPG 3:1 | 10.1038/s41598-020-74401-5 |
| Melittin | `GIGAVLKVLTTGLPALISWIKRKRQQ-NH2` | 26 | +6 | transient aggregates | large transient pores | 10.1038/s41467-024-51691-1 |
| Maculatin 1.1 | `GLFGVLAKVAAHVVPAIAEHF-NH2` | 21 | +1 | dimers to hexamers | pore, carpet at high concentration, POPG:POPE | 10.1007/s00894-018-3747-z |
| Aurein 1.2 | `GLFDIIKKIAESF-NH2` | 13 | +1 | surface clusters | carpet, POPG:POPE | 10.1007/s00894-018-3747-z |
| PGLa | `GMASKAGAIAGKIAKVALKAL-NH2` | 21 | +5 | heterodimers with magainin 2 | hourglass-shaped toroidal pores | 10.1021/jacs.5c11101 |

Uses in a campaign:

| Role | Peptide | Reason |
|---|---|---|
| Positive control, pore | magainin 2 | pore structure solved in POPE:POPG 3:1, the default bacterial composition |
| Positive control, carpet | aurein 1.2 | too short to span the bilayer |
| Multimer start | magainin 2 with `--oligomer-size 6 --arrangement antiparallel`, LL-37 with `--oligomer-size 4 --arrangement antiparallel` | reported stoichiometries |
| Hinge test | melittin, maculatin 1.1 | internal proline, compare ideal helix against an explicit secondary structure string |

Outside the current scope of SHAKER:

| Peptide | Reason |
|---|---|
| Protegrin-1, tachyplesin I | beta-hairpins with disulfide bonds |
| Alamethicin | contains Aib and phenylalaninol, absent from the Martini 3 protein mapping |
| Dermcidin | anionic, channel depends on Zn2+, which the Martini 3 ion file does not provide |
| Magainin 2 with PGLa | hetero-oligomer, SHAKER builds homomultimers only |

## Limits that affect interpretation

| Limit | Effect on SHAKER results |
|---|---|
| Secondary structure is fixed in Martini 3 | the coil to helix transition on membrane binding and partial unfolding at the surface are absent. The peptide is a helix in water as well |
| Ideal helix | hinge flexibility at proline and glycine is not captured |
| Self-assembly start | peptides end on both sides of the bilayer and can be trapped in a transmembrane state while the bilayer forms. In an infection the peptide reaches a formed membrane from one side |
| Oligomer structures from crystals | several were solved in detergent (magainin 2, LL-37). A multimer start tests whether the stoichiometry survives in a bilayer. It does not prove it forms |
| Single bilayer model | no outer membrane. Peptides sequestered by lipopolysaccharide never reach the inner membrane in vivo |
| Coarse-grained time | Martini dynamics are faster than atomistic dynamics by a factor that depends on the process. Times are comparable between SHAKER conditions, not with experiment |

## Source check

The eight DOIs cited in the source notes were looked up in Crossref on 2026-11-12. All resolve, and title, first author, journal and year match the notes. The stoichiometries, lipid ratios and mechanisms in the tables above come from those notes and were not re-read in the papers.

Two citations in the notes carry no DOI and were left out: Park et al. (2023) and Marquette and Bechinger (2018). The notes attach the melittin paper (10.1038/s41467-024-51691-1) to alamethicin as well, which the paper title does not confirm.

## References

| DOI | First author | Journal | Year | Title |
|---|---|---|---|---|
| 10.1038/s41598-025-23539-1 | Sancho-Vaello | Scientific Reports | 2025 | Structure of a barrel-stave pore formed by magainin-2 reveals anion selectivity and zipper-mediated assembly |
| 10.1038/s41598-020-74401-5 | Sancho-Vaello | Scientific Reports | 2020 | The structure of the antimicrobial human cathelicidin LL-37 shows oligomerization and channel formation in the presence of membrane mimics |
| 10.1038/s41467-024-51691-1 | Ulmschneider | Nature Communications | 2024 | Melittin can permeabilize membranes via large transient pores |
| 10.1007/s00894-018-3747-z | Balatti | Journal of Molecular Modeling | 2018 | A coarse-grained approach to studying the interactions of the antimicrobial peptides aurein 1.2 and maculatin 1.1 with POPG/POPE lipid mixtures |
| 10.1021/jacs.5c11101 | Zan | Journal of the American Chemical Society | 2026 | PGLa and Magainin 2 Can Porate Membranes via Transient Hourglass-Shaped Toroidal Pores |
| 10.1073/pnas.1214739110 | Song | PNAS | 2013 | Crystal structure and functional mechanism of a human antimicrobial membrane channel |
| 10.1021/acs.jpcb.7b06591 | Lipkin | The Journal of Physical Chemistry B | 2017 | Transmembrane Pore Structures of β-Hairpin Antimicrobial Peptides by All-Atom Simulations |
| 10.1016/j.bpj.2010.02.024 | Capone | Biophysical Journal | 2010 | Antimicrobial Protegrin-1 Forms Ion Channels: Molecular Dynamic Simulation, Atomic Force Microscopy, and Electrical Conductance Studies |
