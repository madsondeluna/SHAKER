import warnings

import pytest

warnings.filterwarnings("ignore", category=SyntaxWarning)

MAG2 = "GIGKFLHSAKKFGKAFVGEIMNS"


@pytest.fixture(scope="session")
def library():
    from shaker.lipids import LipidLibrary

    return LipidLibrary.load()


@pytest.fixture(scope="session")
def mag2(tmp_path_factory):
    from shaker.peptide import prepare_peptide

    return prepare_peptide("MAG2", MAG2, tmp_path_factory.mktemp("mag2"))
