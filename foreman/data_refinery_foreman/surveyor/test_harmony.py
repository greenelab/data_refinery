from django.test import TestCase, tag

import GEOparse
import vcr

from data_refinery_common.models import Sample
from data_refinery_foreman.surveyor import utils
from data_refinery_foreman.surveyor.array_express import SAMPLES_URL
from data_refinery_foreman.surveyor.harmony import (
    Harmonizer,
    determine_title_field,
    extract_title,
    harmonize_all_samples,
    parse_sdrf,
    preprocess_geo_sample,
)
from data_refinery_foreman.surveyor.sra import SraSurveyor, UnsupportedDataTypeError

GEOparse.logger.set_verbosity("WARN")


class HarmonyTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._harmonizer = Harmonizer()

    @classmethod
    def tearDownClass(cls):
        cls._harmonizer = None

    def setUp(self):
        self.sample = Sample()
        self.sample.save()

        self.samples = [self.sample]

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.harmony.sdrf_harmony.yaml")
    def test_sdrf_harmony(self):
        """ Harmonize SDRF test"""

        metadata = parse_sdrf(
            "https://www.ebi.ac.uk/arrayexpress/files/E-MTAB-3050/E-MTAB-3050.sdrf.txt"
        )
        harmonized = harmonize_all_samples(metadata)

        title = "donor A islets RNA"
        self.assertTrue(title in harmonized.keys())
        self.assertTrue("sex" in harmonized[title].keys())
        self.assertTrue("female" == harmonized[title]["sex"])
        self.assertTrue("age" in harmonized[title].keys())
        self.assertTrue(54.0 == harmonized[title]["age"])
        self.assertTrue("specimen_part" in harmonized[title].keys())
        self.assertTrue("subject" in harmonized[title].keys())
        self.assertTrue("developmental_stage" in harmonized[title].keys())

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.harmony.sdrf_big.yaml")
    @tag("slow")
    def test_sdrf_big(self):
        """ Tests lots of different cases for harmonization"""

        lots = [
            "E-GEOD-59071",
            "E-MTAB-2967",
            "E-GEOD-36807",
            "E-MTAB-184",
            "E-GEOD-22619",
            "E-GEOD-25220",
            "E-GEOD-24287",
            "E-GEOD-13367",
            "E-GEOD-4183",
            "E-GEOD-3365",
            "E-GEOD-57183",
            "E-GEOD-67596",
            "E-GEOD-58667",
            "E-GEOD-55319",
            "E-GEOD-26112",
            "E-GEOD-41831",
            "E-GEOD-41744",
            "E-GEOD-26554",
            "E-GEOD-20307",
            "E-GEOD-23687",
            "E-GEOD-21521",
            "E-GEOD-13501",
            "E-GEOD-13849",
            "E-GEOD-15645",
            "E-GEOD-11083",
            "E-GEOD-15083",
            "E-GEOD-11907",
            "E-GEOD-8650",
            "E-GEOD-7753",
            "E-GEOD-68004",
            "E-GEOD-63881",
            "E-GEOD-48498",
            "E-GEOD-16797",
            "E-MTAB-5542",
            "E-GEOD-81622",
            "E-GEOD-65391",
            "E-GEOD-72798",
            "E-GEOD-72747",
            "E-GEOD-78193",
            "E-GEOD-62764",
            "E-GEOD-45291",
            "E-GEOD-50772",
            "E-GEOD-61635",
            "E-GEOD-45923",
            "E-GEOD-49454",
            "E-GEOD-29536",
            "E-GEOD-52471",
            "E-GEOD-50635",
            "E-GEOD-37463",
            "E-GEOD-37460",
            "E-GEOD-37455",
            "E-GEOD-39088",
            "E-GEOD-32591",
            "E-GEOD-36941",
            "E-GEOD-32279",
            "E-GEOD-26975",
            "E-GEOD-24060",
            "E-GEOD-24706",
            "E-MEXP-1635",
            "E-MTAB-5262",
            "E-GEOD-72246",
            "E-GEOD-80047",
            "E-GEOD-69967",
            "E-GEOD-82140",
            "E-GEOD-75890",
            "E-GEOD-67853",
            "E-GEOD-75343",
            "E-GEOD-50614",
            "E-GEOD-57376",
            "E-GEOD-61281",
            "E-GEOD-47751",
            "E-GEOD-58121",
            "E-GEOD-51440",
            "E-GEOD-55201",
            "E-GEOD-53552",
            "E-GEOD-50790",
            "E-GEOD-47598",
            "E-GEOD-41664",
            "E-GEOD-41663",
            "E-GEOD-41662",
            "E-GEOD-34248",
            "E-GEOD-30999",
            "E-GEOD-31652",
            "E-GEOD-30768",
            "E-GEOD-27887",
            "E-GEOD-18948",
            "E-GEOD-11903",
            "E-GEOD-13355",
            "E-GEOD-2737",
            "E-GEOD-78068",
            "E-GEOD-74143",
            "E-GEOD-58795",
            "E-GEOD-48780",
            "E-GEOD-55584",
            "E-GEOD-55457",
            "E-GEOD-55235",
            "E-GEOD-35455",
            "E-GEOD-45867",
            "E-GEOD-30023",
            "E-GEOD-42296",
            "E-GEOD-39340",
            "E-GEOD-37107",
            "E-GEOD-33377",
            "E-MEXP-3390",
            "E-GEOD-25160",
            "E-GEOD-24742",
            "E-GEOD-15573",
            "E-MTAB-11",
            "E-GEOD-15258",
            "E-GEOD-15602",
            "E-GEOD-12021",
            "E-GEOD-8350",
            "E-GEOD-1402",
            "E-GEOD-56998",
            "E-GEOD-42832",
            "E-GEOD-37912",
            "E-GEOD-32887",
            "E-GEOD-19314",
            "E-GEOD-18781",
            "E-GEOD-16538",
            "E-GEOD-66795",
            "E-GEOD-40568",
            "E-MTAB-2073",
            "E-GEOD-51092",
            "E-GEOD-48378",
            "E-GEOD-40611",
            "E-GEOD-23117",
            "E-MEXP-1883",
            "E-GEOD-81292",
            "E-GEOD-76886",
            "E-GEOD-76809",
            "E-GEOD-65405",
            "E-GEOD-65336",
            "E-GEOD-58095",
            "E-MEXP-1214",
            "E-GEOD-48149",
            "E-MEXP-32",
            "E-GEOD-33463",
            "E-GEOD-32413",
            "E-GEOD-19617",
            "E-MTAB-1944",
            "E-GEOD-17114",
            "E-GEOD-44719",
        ]

        for accession in lots:
            metadata = parse_sdrf(
                "https://www.ebi.ac.uk/arrayexpress/files/"
                + accession
                + "/"
                + accession
                + ".sdrf.txt"
            )
            if not metadata:
                continue
            harmonized = harmonize_all_samples(metadata)
            self.assertIsNotNone(harmonized)

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.harmony.sra_harmony.yaml")
    def test_sra_harmony(self):
        """
        Tests a specific harmonization from SRA
        """

        metadata = SraSurveyor.gather_all_metadata("SRR1533126")
        harmonized_sample = self._harmonizer.harmonize_sample(metadata)
        title = "Phosphaturic mesenchymal tumour (PMT) case 2 of NTUH"
        self.assertEqual(title, harmonized_sample["title"])
        self.assertTrue("sex" in harmonized_sample.keys())
        self.assertEqual("female", harmonized_sample["sex"])

        self.assertTrue("age" in harmonized_sample.keys())
        self.assertEqual(57.0, harmonized_sample["age"])

        self.assertTrue("specimen_part" in harmonized_sample.keys())
        self.assertTrue("disease" in harmonized_sample.keys())

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.harmony.sra_lots.yaml")
    @tag("slow")
    def test_sra_lots(self):
        """
        Smoke tests a few SRA types
        """

        # These can be built via
        #    https://www.ncbi.nlm.nih.gov/sra
        # Searching for
        #    (human) NOT cluster_dbgap[PROP]
        # And then Sent To -> File -> Accession List
        lots = [
            "ERR188021",
            "ERR188022",
            "ERR205021",
            "ERR205022",
            "ERR205023",
            "SRR000001",  # Soft fail, bad platform
            "ERR1737666",
            "ERR030891",
            "ERR030892",
            "SRR1542948",
            "SRR1553477",
            "SRR1542330",
            "SRR1538698",
            "SRR1538760",
            "SRR1538866",
            "SRR1539218",
            "SRR1797277",
            "SRR1533126",
        ]
        for accession in lots:
            try:
                metadata = SraSurveyor.gather_all_metadata(accession)
                harmonized = harmonize_all_samples([metadata])
                self.assertIsNotNone(harmonized)
            except UnsupportedDataTypeError:
                continue

    def test_geo_harmony(self):
        """
        Thoroughly tests a specific GEO harmonization
        """
        # Weird ones caused bugs
        gse = GEOparse.get_GEO("GSE94532", destdir="/tmp/GSE94532/", silent=True)
        for _, sample in gse.gsms.items():
            preprocess_geo_sample(sample)

        # Illumina
        gse = GEOparse.get_GEO("GSE32628", destdir="/tmp/GSE32628/", silent=True)

        title = "SCC_P-57"
        test_sample = None
        for _, sample in gse.gsms.items():
            preprocessed_sample = preprocess_geo_sample(sample)
            harmonized_sample = self._harmonizer.harmonize_sample(preprocessed_sample)

            if sample.metadata["title"][0] == title:
                test_sample = harmonized_sample

        self.assertEqual(title, test_sample["title"])
        self.assertIn("sex", test_sample.keys())
        self.assertIn("age", test_sample.keys())
        self.assertIn("specimen_part", test_sample.keys())
        self.assertIn("subject", test_sample.keys())

        # Agilent Two Color
        gse = GEOparse.get_GEO("GSE93857", destdir="/tmp", silent=True)
        for _, sample in gse.gsms.items():
            preprocessed_sample = preprocess_geo_sample(sample)
            harmonized_sample = self._harmonizer.harmonize_sample(preprocessed_sample)

        self.assertIn("title", harmonized_sample.keys())
        self.assertIn("treatment", harmonized_sample.keys())
        self.assertIn("specimen_part", harmonized_sample.keys())
        self.assertIn("time", harmonized_sample.keys())

        gse = GEOparse.get_GEO("GSE103060", destdir="/tmp", silent=True)
        for _, sample in gse.gsms.items():
            preprocessed_sample = preprocess_geo_sample(sample)
            harmonized_sample = self._harmonizer.harmonize_sample(preprocessed_sample)

        self.assertIn("title", harmonized_sample.keys())
        self.assertIn("specimen_part", harmonized_sample.keys())

    def test_geo_leg_cancer(self):
        """Related:
        https://github.com/AlexsLemonade/refinebio/issues/165#issuecomment-383969447
        """
        gse = GEOparse.get_GEO("GSE32628", destdir="/tmp/GSE32628/", silent=True)

        title = "SCC_P-57"
        test_sample = None
        for _, sample in gse.gsms.items():
            preprocessed_sample = preprocess_geo_sample(sample)
            harmonized_sample = self._harmonizer.harmonize_sample(preprocessed_sample)

            if sample.metadata["title"][0] == title:
                test_sample = harmonized_sample

        self.assertEqual(title, test_sample["title"])
        self.assertTrue("keratinocyte" == test_sample["specimen_part"])
        self.assertTrue("squamous cell carcinoma" == test_sample["disease"])
        self.assertTrue("azathioprine + prednison" == test_sample["compound"])

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.harmony.ordering_mismatch.yaml")
    def test_ordering_mismatch(self):
        """Makes sure that the order samples' keys are in does not affect the title chosen.

        Related: https://github.com/AlexsLemonade/refinebio/pull/304
        """
        experiment_accession_code = "E-TABM-38"

        samples_endpoint = SAMPLES_URL.format(experiment_accession_code)
        r = utils.requests_retry_session().get(samples_endpoint, timeout=60)
        json_samples = r.json()["experiment"]["sample"]
        flattened_json_samples = [utils.flatten(json_sample) for json_sample in json_samples]

        SDRF_URL_TEMPLATE = "https://www.ebi.ac.uk/arrayexpress/files/{code}/{code}.sdrf.txt"
        sdrf_url = SDRF_URL_TEMPLATE.format(code=experiment_accession_code)
        parsed_samples = parse_sdrf(sdrf_url)

        title_field = determine_title_field(parsed_samples, flattened_json_samples)
        sdrf_samples = harmonize_all_samples(parsed_samples, title_field)
        json_titles = [
            extract_title(json_sample, title_field) for json_sample in flattened_json_samples
        ]

        # The titles won't match up if the order of the sample dicts
        # isn't corrected for, resulting in a KeyError being raised.
        # So if this doesn't raise a KeyError, then we're good.
        for title in json_titles:
            sdrf_samples[title]
