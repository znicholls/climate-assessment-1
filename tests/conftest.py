import hashlib
import os
import os.path
import shutil
from pprint import pprint

import pandas as pd
import pandas.testing as pdt
import pooch
import pyam
import pytest
import requests
import scmdata

TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), "test-data")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@pytest.fixture(scope="session")
def test_data_dir():
    return TEST_DATA_DIR


@pytest.fixture(scope="session")
def data_dir():
    return DATA_DIR


STARTING_POINT_CSV_FILE = os.path.join(TEST_DATA_DIR, "starting-point-test-data.csv")


@pytest.fixture(scope="session")
def test_starting_point_df():
    return pyam.IamDataFrame(STARTING_POINT_CSV_FILE)


@pytest.fixture(scope="function")
def test_downscale_df(request):
    return pyam.IamDataFrame(request.cls.tdownscale_df)


@pytest.fixture(scope="function")
def test_db(request):
    return pyam.IamDataFrame(request.cls.tdb)


@pytest.fixture(scope="module")
def ar6_emissions(test_data_dir):
    return pyam.IamDataFrame(os.path.join(TEST_DATA_DIR, "ex2.csv")).filter(
        model="model1"
    )


@pytest.fixture(scope="module")
def sr15_emissions():
    return pyam.IamDataFrame(
        os.path.join(DATA_DIR, "sr15-411/sr15_scenarios.csv")
    ).filter(model="AIM_2_0", scenario="ADVANCE_2020_1.5C-2100")


@pytest.fixture(scope="module")
def rcmip_emissions():
    fname = os.path.join(DATA_DIR, "rcmip-emissions-annual-means-v5-1-0_ssp245.csv")

    run = scmdata.ScmRun(fname)
    run["variable"] = (
        run["variable"]
        .apply(lambda x: x.replace("MAGICC AFOLU", "AFOLU"))
        .apply(
            lambda x: x.replace(
                "MAGICC Fossil and Industrial", "Energy and Industrial Processes"
            )
        )
        .apply(lambda x: x.replace("Emissions|F-Gases|HFC", "Emissions|HFC"))
        .apply(lambda x: x.replace("Emissions|F-Gases|PFC", "Emissions|PFC"))
        .apply(lambda x: x.replace("HFC4310mee", "HFC43-10"))
        .apply(lambda x: x.replace("F-Gases|SF6", "SF6"))
    )
    return run


def pytest_addoption(parser):
    parser.addoption(
        "--update-expected-files",
        action="store_true",
        default=False,
        help="Overwrite expected files",
    )


@pytest.fixture
def update_expected_files(request):
    return request.config.getoption("--update-expected-files")


def _get_filename(emissions_id, suffix):
    return "{}{}".format(emissions_id, suffix)


@pytest.fixture
def check_workflow_output():
    def check_func(
        emissions_id,
        outdir,
        regressiondir,
        model,
        model_version,
        update_expected_files,
        rtol=1e-4,
    ):
        """
        Check the climate assessment workflow output
        """
        alloutput_suffix = "_alloutput.xlsx"
        alloutput_meta_suffix = "_alloutput_meta.csv"

        # save the alloutput meta as a csv so version control can more easily
        # track the changes
        alloutput_filename = _get_filename(emissions_id, alloutput_suffix)
        alloutput = pyam.IamDataFrame(os.path.join(outdir, alloutput_filename))
        alloutput_meta = alloutput.meta.sort_index()
        alloutput_meta_filename = _get_filename(emissions_id, alloutput_meta_suffix)
        alloutput_meta.to_csv(os.path.join(outdir, alloutput_meta_filename))

        # save the exceedance probabilities as a csv so version control can more easily
        # track the changes
        exceedance_probabilities_suffix = "_full_exceedance_probabilities.xlsx"
        exceedance_probabilities_suffix_csv = exceedance_probabilities_suffix.replace(
            ".xlsx", ".csv"
        )
        exceedance_probabilities_filename = _get_filename(
            emissions_id, exceedance_probabilities_suffix
        )
        exceedance_probabilities = pd.read_excel(
            os.path.join(outdir, exceedance_probabilities_filename)
        )
        exceedance_probabilities = exceedance_probabilities.drop(
            "Unnamed: 0", axis="columns"
        )
        exceedance_probabilities = exceedance_probabilities.sort_index()
        exceedance_probabilities.to_csv(
            os.path.join(
                outdir, _get_filename(emissions_id, exceedance_probabilities_suffix_csv)
            ),
            index=False,
        )

        suffixes = [
            "_harmonized_infilled.csv",
            alloutput_suffix,
            alloutput_meta_suffix,
            "_IAMC_climateassessment0000.csv",
            "_IAMC_climateassessment.xlsx",
            exceedance_probabilities_suffix,
            exceedance_probabilities_suffix_csv,
        ]
        for suffix in suffixes:
            filename = _get_filename(emissions_id, suffix)

            file_to_check = os.path.join(outdir, filename)

            file_expected = os.path.join(regressiondir, filename)
            if update_expected_files:
                helper_csv = any(
                    [
                        file_to_check.endswith(s)
                        for s in [
                            alloutput_meta_suffix,
                            exceedance_probabilities_suffix_csv,
                        ]
                    ]
                )
                if helper_csv:
                    shutil.copyfile(file_to_check, file_expected)
                elif file_to_check.endswith(".csv"):
                    # ensure file is written with sorted index so diffs are easy
                    # to see
                    tmp = pyam.IamDataFrame(file_to_check)
                    out = tmp.timeseries().sort_index().reset_index()
                    out = out.rename(columns={c: str(c).title() for c in out.columns})
                    out.to_csv(file_expected, index=False)
                else:
                    shutil.copyfile(file_to_check, file_expected)
                continue

            print("Checking {}".format(file_to_check))
            if "_full_exceedance_probabilities" in suffix:

                def load_exceedance_probs(fp):
                    if fp.endswith(".csv"):
                        loaded = pd.read_csv(fp)
                    else:
                        loaded = pd.read_excel(fp).drop(["Unnamed: 0"], axis="columns")

                    return loaded.set_index(["model", "scenario"])

                res = load_exceedance_probs(file_to_check)
                exp = load_exceedance_probs(file_expected)

                pdt.assert_frame_equal(res, exp, check_like=True)
                continue

            def check_alloutput_meta_cols(idf, model_version_str, pyam_df=True):
                """
                Check the meta cols are what we expect, then drop them because
                the versions can change and it doesn't matter for output
                """
                expected_meta_cols_to_drop = [
                    "climate-models",
                    "infilling",
                    "workflow",
                    "harmonization",
                ]
                cols_to_keep = [
                    "exclude",
                    "Category",
                    "Category_name",
                    "Exceedance Probability 1.5C ({})".format(model_version_str),
                    "Exceedance Probability 2.0C ({})".format(model_version_str),
                    "Exceedance Probability 2.5C ({})".format(model_version_str),
                    "Exceedance Probability 3.0C ({})".format(model_version_str),
                    "Exceedance Probability 3.5C ({})".format(model_version_str),
                    "Exceedance Probability 4.0C ({})".format(model_version_str),
                    "Exceedance Probability 4.5C ({})".format(model_version_str),
                    "Exceedance Probability 5.0C ({})".format(model_version_str),
                    "p5 peak warming ({})".format(model_version_str),
                    "p10 peak warming ({})".format(model_version_str),
                    "p17 peak warming ({})".format(model_version_str),
                    "p25 peak warming ({})".format(model_version_str),
                    "p33 peak warming ({})".format(model_version_str),
                    "median peak warming ({})".format(model_version_str),
                    "p66 peak warming ({})".format(model_version_str),
                    "p67 peak warming ({})".format(model_version_str),
                    "p75 peak warming ({})".format(model_version_str),
                    "p83 peak warming ({})".format(model_version_str),
                    "p90 peak warming ({})".format(model_version_str),
                    "p95 peak warming ({})".format(model_version_str),
                    "p5 warming in 2100 ({})".format(model_version_str),
                    "p10 warming in 2100 ({})".format(model_version_str),
                    "p17 warming in 2100 ({})".format(model_version_str),
                    "p25 warming in 2100 ({})".format(model_version_str),
                    "p33 warming in 2100 ({})".format(model_version_str),
                    "median warming in 2100 ({})".format(model_version_str),
                    "p66 warming in 2100 ({})".format(model_version_str),
                    "p67 warming in 2100 ({})".format(model_version_str),
                    "p75 warming in 2100 ({})".format(model_version_str),
                    "p83 warming in 2100 ({})".format(model_version_str),
                    "p90 warming in 2100 ({})".format(model_version_str),
                    "p95 warming in 2100 ({})".format(model_version_str),
                    "p5 year of peak warming ({})".format(model_version_str),
                    "p10 year of peak warming ({})".format(model_version_str),
                    "p17 year of peak warming ({})".format(model_version_str),
                    "p25 year of peak warming ({})".format(model_version_str),
                    "p33 year of peak warming ({})".format(model_version_str),
                    "median year of peak warming ({})".format(model_version_str),
                    "p66 year of peak warming ({})".format(model_version_str),
                    "p67 year of peak warming ({})".format(model_version_str),
                    "p75 year of peak warming ({})".format(model_version_str),
                    "p83 year of peak warming ({})".format(model_version_str),
                    "p90 year of peak warming ({})".format(model_version_str),
                    "p95 year of peak warming ({})".format(model_version_str),
                ]

                if pyam_df:
                    cols_to_drop = list(set(idf.meta.columns) - set(cols_to_keep))
                else:
                    cols_to_drop = list(set(idf.columns) - set(cols_to_keep))

                assert set(cols_to_drop) == set(
                    expected_meta_cols_to_drop
                ), "{} not equal to {}".format(
                    set(cols_to_drop), set(expected_meta_cols_to_drop)
                )

                if pyam_df:
                    idf.meta = idf.meta[cols_to_keep]
                else:
                    idf = idf[cols_to_keep]

                return idf

            if model.upper() == "MAGICC":
                model_version_str = "MAGICC{}".format(model_version)

            elif model.upper() == "FAIR":
                model_version_str = "FaIRv{}".format(model_version)

            elif model.upper() == "CICERO-SCM":
                model_version_str = "CICERO-SCM"

            else:
                raise NotImplementedError(model)

            if suffix == alloutput_meta_suffix:
                res = pd.read_csv(file_to_check).set_index(["model", "scenario"])
                exp = pd.read_csv(file_expected).set_index(["model", "scenario"])

                res = check_alloutput_meta_cols(res, model_version_str, pyam_df=False)
                exp = check_alloutput_meta_cols(exp, model_version_str, pyam_df=False)

                pdt.assert_frame_equal(res, exp, check_dtype=False, check_like=True)
                continue

            res = pyam.IamDataFrame(file_to_check)
            exp = pyam.IamDataFrame(file_expected)

            if "alloutput" in suffix:

                res = check_alloutput_meta_cols(res, model_version_str)
                exp = check_alloutput_meta_cols(exp, model_version_str)

            diff = pyam.compare(res, exp, rtol=rtol, atol=1e-4)
            if not diff.empty:
                pprint(
                    sorted(diff.index.get_level_values("variable").unique().tolist())
                )
                raise AssertionError(diff)

            pdt.assert_frame_equal(
                res.meta, exp.meta, check_dtype=False, check_like=True
            )

    return check_func


@pytest.fixture
def check_consistency_with_database():
    def check_func(
        output_file,
        expected_output_file,
        rtol=1e-5,
        atol=1e-6,
    ):
        compare_index = ["Model", "Scenario", "Region", "Variable", "Unit"]
        test_output = pd.read_excel(output_file).set_index(compare_index)
        expected_db_output = pd.read_excel(expected_output_file).set_index(
            compare_index
        )

        # can be removed if we add extra output to the database
        test_output = test_output.loc[expected_db_output.index, :]

        pdt.assert_frame_equal(
            test_output,
            expected_db_output,
            rtol=rtol,
            atol=atol,
        )

    return check_func


def get_infiller_download_link(filename):
    pyam.iiasa.set_config(
        os.environ.get("SCENARIO_EXPLORER_USER"),
        os.environ.get("SCENARIO_EXPLORER_PASSWORD"),
        "iiasa_creds.yaml",
    )
    try:
        conn = pyam.iiasa.Connection(
            creds="iiasa_creds.yaml",
            auth_url="https://db1.ene.iiasa.ac.at/EneAuth/config/v1",
        )
    finally:
        # remove the yaml cred file
        os.remove("iiasa_creds.yaml")

    infiller_url = (
        "https://db1.ene.iiasa.ac.at/ar6-public-api/rest/v2.1/files/"
        f"{filename}?redirect=false"
    )
    return requests.get(
        infiller_url,
        headers={"Authorization": f"Bearer {conn._token}"},
    ).json()["directLink"]


def file_available_or_downloaded(filepath, hash_exp, url):
    """
    Check if file exists (and matches expected hash) or can be downloaded

    Parameters
    ----------
    filepath : str
        Path to file

    hash_exp : str
        Expected md5 hash

    url : str
        URL from which to download the file if it doesn't exist

    Returns
    -------
    bool
        Is the file available (or has it been downloaded hence is now
        available)?
    """

    def local_file_exists_and_matches_hash():
        return (
            os.path.isfile(filepath)
            and hashlib.md5(open(filepath, "rb").read()).hexdigest() == hash_exp
        )

    if local_file_exists_and_matches_hash():
        return True

    try:
        pooch.retrieve(
            url=url,
            known_hash=f"md5:{hash_exp}",
            path=os.path.dirname(filepath),
            fname=os.path.basename(filepath),
        )
    except Exception as exc:
        # probably better ways to do this, can iterate as we use
        print(str(exc))
        return False

    if not local_file_exists_and_matches_hash():
        # probably should be error rather than print...
        print("Weird, download seemed to work but file doesn't exist or match hash")
        return False

    return True


@pytest.fixture(scope="session")
def infiller_database_filepath():
    INFILLER_DATABASE_NAME = (
        "1652361598937-ar6_emissions_vetted_infillerdatabase_10.5281-zenodo.6390768.csv"
    )
    INFILLER_HASH = "30fae0530d76cbcb144f134e9ed0051f"
    INFILLER_DATABASE_DOWNLOAD_URL = get_infiller_download_link(INFILLER_DATABASE_NAME)
    INFILLER_DATABASE_FILEPATH = os.path.join(TEST_DATA_DIR, INFILLER_DATABASE_NAME)

    if not file_available_or_downloaded(
        INFILLER_DATABASE_FILEPATH,
        INFILLER_HASH,
        INFILLER_DATABASE_DOWNLOAD_URL,
    ):
        pytest.skip(
            "The ar6 infiller database was not found. Therefore this test "
            "will be skipped. If you want to run the test you can download the required file "
            "from https://data.ece.iiasa.ac.at/ar6/#/downloads under 'Infiller database for "
            "silicone: IPCC AR6 WGIII version (DOI: 10.5281/zenodo.6390768)'. "
            f"Place it into: {TEST_DATA_DIR} **without** changing its name and the tests "
            "should run again."
        )

    return INFILLER_DATABASE_FILEPATH


@pytest.fixture(scope="session")
def fair_slim_configs_filepath():
    FAIR_SLIM_CONFIGS_FILENAME = "fair-1.6.2-wg3-params-slim.json"
    FAIR_SLIM_CONFIGS_HASH = "c071ca619c0ae37a6abdeb79c0cece7b"
    FAIR_SLIM_CONFIGS_DOWNLOAD_URL = "https://zenodo.org/record/6601980/files/fair-1.6.2-wg3-params-slim.json?download=1"
    FAIR_SLIM_CONFIGS_FILEPATH = os.path.join(TEST_DATA_DIR, FAIR_SLIM_CONFIGS_FILENAME)

    if not file_available_or_downloaded(
        FAIR_SLIM_CONFIGS_FILEPATH,
        FAIR_SLIM_CONFIGS_HASH,
        FAIR_SLIM_CONFIGS_DOWNLOAD_URL,
    ):
        pytest.skip("FaIR's slim config is not available")

    return FAIR_SLIM_CONFIGS_FILEPATH


@pytest.fixture(scope="session")
def fair_common_configs_filepath():
    FAIR_COMMON_CONFIGS_FILENAME = "fair-1.6.2-wg3-params-common.json"
    FAIR_COMMON_CONFIGS_HASH = "42ccaffcd3dea88edfca77da0cd5789b"
    FAIR_COMMON_CONFIGS_DOWNLOAD_URL = "https://zenodo.org/record/6601980/files/fair-1.6.2-wg3-params-common.json?download=1"
    FAIR_COMMON_CONFIGS_FILEPATH = os.path.join(
        TEST_DATA_DIR, FAIR_COMMON_CONFIGS_FILENAME
    )

    if not file_available_or_downloaded(
        FAIR_COMMON_CONFIGS_FILEPATH,
        FAIR_COMMON_CONFIGS_HASH,
        FAIR_COMMON_CONFIGS_DOWNLOAD_URL,
    ):
        pytest.skip("FaIR's common config is not available")

    return FAIR_COMMON_CONFIGS_FILEPATH
