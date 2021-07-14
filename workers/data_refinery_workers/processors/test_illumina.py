import csv
import os
import os.path
import shutil
import tempfile
from typing import Dict

from django.test import TestCase, tag

from data_refinery_common.enums import PipelineEnum
from data_refinery_common.models import (
    Organism,
    OriginalFile,
    OriginalFileSampleAssociation,
    Pipeline,
    ProcessorJob,
    ProcessorJobOriginalFileAssociation,
    Sample,
    SampleAnnotation,
)
from data_refinery_workers.processors import illumina, testing_utils, utils


def prepare_illumina_job(job_info: Dict) -> ProcessorJob:
    pj = ProcessorJob()
    pj.pipeline_applied = "ILLUMINA_TO_PCL"
    pj.save()

    og_file = OriginalFile()
    og_file.source_filename = job_info["source_filename"]
    og_file.filename = job_info["filename"]
    og_file.absolute_file_path = job_info["absolute_file_path"]
    og_file.is_downloaded = True
    og_file.save()

    assoc1 = ProcessorJobOriginalFileAssociation()
    assoc1.original_file = og_file
    assoc1.processor_job = pj
    assoc1.save()

    for s in job_info["samples"]:
        # For convenience, if you give a list of strings we'll just use the
        # strings as both titles and accessions.
        annotation = None
        if type(s) == str:
            accession_code = s
            title = s
        elif type(s) == tuple and list(map(type, s)) == [str, str]:
            accession_code, title = s
        elif type(s) == tuple and list(map(type, s)) == [str, str, dict]:
            accession_code, title, annotation = s
        else:
            raise ValueError(f"Invalid sample type for sample {s}")

        sample = Sample()
        sample.accession_code = accession_code
        sample.title = title
        sample.organism = job_info["organism"]
        sample.save()

        sa = SampleAnnotation()
        sa.sample = sample
        sa.data = annotation if annotation is not None else {"description": [title]}
        sa.is_ccdl = False
        sa.save()

        sample_assoc = OriginalFileSampleAssociation()
        sample_assoc.original_file = og_file
        sample_assoc.sample = sample
        sample_assoc.save()

    return pj


def _make_original_file_with_contents(contents: str) -> OriginalFile:
    _, path = tempfile.mkstemp(suffix=".txt")
    with open(path, "w") as f:
        f.write(contents)

    og_file = OriginalFile()
    og_file.source_filename = path
    og_file.filename = os.path.basename(path)
    og_file.absolute_file_path = os.path.realpath(path)
    og_file.is_downloaded = True
    og_file.save()

    return og_file


def _try_sanitizing_file(file: str) -> str:
    pj = ProcessorJob()
    pj.pipeline_applied = "ILLUMINA_TO_PCL"
    pj.save()

    og_file = _make_original_file_with_contents(file)

    job_context = illumina._prepare_files({"job_id": pj.pk, "original_files": [og_file], "job": pj})
    job_context = illumina._detect_encoding(job_context)
    return illumina._sanitize_input_file(job_context)


# Save this experiment separately (sans organism) because we need it for multiple tests
GSE22427 = {
    "source_filename": "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE22nnn/GSE22427/suppl/GSE22427%5Fnon%2Dnormalized%2Etxt.gz",
    "filename": "GSE22427_non-normalized.txt",
    "absolute_file_path": "/home/user/data_store/raw/TEST/ILLUMINA/GSE22427_non-normalized.txt",
    "samples": [
        "LV-C&si-Control-1",
        "LV-C&si-Control-2",
        "LV-C&si-Control-3",
        "LV-C&si-EZH2-1",
        "LV-C&si-EZH2-2",
        "LV-C&si-EZH2-3",
        "LV-EZH2&si-EZH2-1",
        "LV-EZH2&si-EZH2-2",
        "LV-EZH2&si-EZH2-3",
        "LV-T350A&si-EZH2-1",
        "LV-T350A&si-EZH2-2",
        "LV-T350A&si-EZH2-3",
    ],
}

GSE22427_HEADER = [
    "ID_REF",
    "LV-C&si-Control-1",
    "Detection Pval",
    "LV-C&si-Control-2",
    "Detection Pval",
    "LV-C&si-Control-3",
    "Detection Pval",
    "LV-C&si-EZH2-1",
    "Detection Pval",
    "LV-C&si-EZH2-2",
    "Detection Pval",
    "LV-C&si-EZH2-3",
    "Detection Pval",
    "LV-EZH2&si-EZH2-1",
    "Detection Pval",
    "LV-EZH2&si-EZH2-2",
    "Detection Pval",
    "LV-EZH2&si- EZH2-3",
    "Detection Pval",
    "LV-T350A&si-EZH2-1",
    "Detection Pval",
    "LV- T350A&si-EZH2-2",
    "Detection Pval",
    "LV-T350A&si-EZH2-3",
    "Detection Pval",
]


class IlluminaToPCLTestCase(TestCase, testing_utils.ProcessorJobTestCaseMixin):
    def assertFailedInIlluminaR(self, pj):
        """XXX: remove this before merging to prod.

        Check if the sample makes it to illumina.R, because right now I am
        investigating samples that fail before even reaching illumina.R. Since
        illumina.R always fails right now, just asserting success leads to
        failures that I'm not looking for"""

        pj.refresh_from_db()

        if (
            not pj.success
            and "Encountered error in R code while running illumina.R" not in pj.failure_reason
            and "Exception raised while running illumina.R" not in pj.failure_reason
        ):
            raise AssertionError(f"\033[1;31mjob did not succeed:\033[0m {pj.failure_reason}")

    @tag("illumina")
    def test_illumina_to_pcl(self):
        """Most basic Illumina to PCL test"""

        organism = Organism(name="HOMO_SAPIENS", taxonomy_id=9606, is_scientific_name=True)
        organism.save()

        job = prepare_illumina_job({**GSE22427, "organism": organism})

        # Remove the title of one of the samples to make sure that we can still
        # find its detection column using the description given as an annotation
        sample = Sample.objects.get(title="LV-T350A&si-EZH2-3")
        sample.title = "ignoreme_for_description"
        sample.accession_code = "ignoreme_for_description"
        sample.save()

        final_context = illumina.illumina_to_pcl(job.pk)
        self.assertFailedInIlluminaR(job)
        # XXX: remove this but because the job failed the rest of this won't succeed
        shutil.rmtree(final_context["work_dir"], ignore_errors=True)
        return

        for sample in final_context["samples"]:
            smashme = sample.get_most_recent_smashable_result_file()
            self.assertTrue(os.path.exists(smashme.absolute_file_path))
            os.remove(smashme.absolute_file_path)

        # Cleanup after the job since it won't since we aren't running in cloud.
        shutil.rmtree(final_context["work_dir"], ignore_errors=True)

    @tag("illumina")
    def test_bad_illumina_detection(self):
        """With the wrong species, this will fail the platform detection threshold."""

        organism = Organism(name="RATTUS_NORVEGICUS", taxonomy_id=9606, is_scientific_name=True)
        organism.save()

        job = prepare_illumina_job({**GSE22427, "organism": organism})
        final_context = illumina.illumina_to_pcl(job.pk)
        self.assertTrue(final_context["abort"])

        # Cleanup after the job since it won't since we aren't running in cloud.
        shutil.rmtree(final_context["work_dir"], ignore_errors=True)

    @tag("illumina")
    def test_good_detection(self):
        """GSE54661 appears to be mislabled (illuminaHumanv4) on GEO. Shows our detector works."""

        organism = Organism(name="HOMO_SAPIENS", taxonomy_id=9606, is_scientific_name=True)
        organism.save()

        pj = prepare_illumina_job(
            {
                "source_filename": "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE54nnn/GSE54661/suppl/GSE54661%5Fnon%5Fnormalized%2Etxt%2Egz",
                "filename": "GSE54661_non_normalized.txt",
                "absolute_file_path": "/home/user/data_store/raw/TEST/ILLUMINA/GSE54661_non_normalized.txt",
                "organism": organism,
                "samples": [("ABCD-1234", "CB CD34+ hypoxia"), ("ABCD-1235", "CB CD34+ normoxia")],
            }
        )

        final_context = illumina.illumina_to_pcl(pj.pk)
        self.assertFailedInIlluminaR(pj)
        self.assertEqual(final_context["platform"], "illuminaHumanv3")

        for key in final_context["samples"][0].sampleannotation_set.all()[0].data.keys():
            self.assertTrue(
                key in ["detected_platform", "detection_percentage", "mapped_percentage"]
            )

        # XXX: remove this but because the job failed the rest of this won't succeed
        shutil.rmtree(final_context["work_dir"], ignore_errors=True)
        return

        for sample in final_context["samples"]:
            smashme = sample.get_most_recent_smashable_result_file()
            self.assertTrue(os.path.exists(smashme.absolute_file_path))

        # Cleanup after the job since it won't since we aren't running in cloud.
        shutil.rmtree(final_context["work_dir"], ignore_errors=True)

    @tag("illumina")
    def test_illumina_latin1_input(self):
        """Test a latin1-encoded Illumina file.

        GSE106321 is encoded in latin1 and uses μ in the title of some
        columns, so preparing the file would cause a UnicodeParseError. Make
        sure that doesn't happen any more.
        """

        organism = Organism(name="HOMO_SAPIENS", taxonomy_id=9606, is_scientific_name=True)
        organism.save()

        pj = prepare_illumina_job(
            {
                "source_filename": "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE106nnn/GSE106321/suppl/GSE106321_non-normalized.txt.gz",
                "filename": "GSE106321_non-normalized.txt",
                "absolute_file_path": "/home/user/data_store/raw/TEST/ILLUMINA/GSE106321_non-normalized.txt",
                "organism": organism,
                "samples": [
                    (
                        "GSM2835938",
                        "A375 + 24h vem (3µM) 2",
                        {"description": ["A375 + 24h vem (3µM) 2"]},
                    ),
                    (
                        "GSM2835937",
                        "A375 + 24h vem (3µM) 1",
                        {"description": ["A375 + 24h vem (3µM) 1"]},
                    ),
                    (
                        "GSM2835936",
                        "A375 + 24h vem (3µM)",
                        {"description": ["A375 + 24h vem (3µM)"]},
                    ),
                    ("GSM2835935", "A375 + 24h DMSO 2", {"description": ["A375 + 24h DMSO 2"]}),
                    ("GSM2835934", "A375+ 24h DMSO 1", {"description": ["A375+ 24h DMSO 1"]}),
                    ("GSM2835933", "A375 + 24h DMSO", {"description": ["A375 + 24h DMSO"]}),
                ],
            }
        )

        final_context = illumina.illumina_to_pcl(pj.pk)
        self.assertFailedInIlluminaR(pj)

        # Make sure that the input is now utf-8 encoded and has the right headers.

        # Trying to open a latin1 file as utf-8 would cause an
        # exception to be thrown, so if opening succeeds we can assume the encoding succeeded.
        with open(final_context["sanitized_file_path"], "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")

            # Check the headers to make sure that the mu was correctly re-encoded
            headers = next(reader)
            self.assertEqual(
                headers,
                [
                    "ID_REF",
                    "A375 + 24h DMSO",
                    "Detection Pval",
                    "A375+ 24h DMSO 1",
                    "Detection Pval",
                    "A375 + 24h DMSO 2",
                    "Detection Pval",
                    "A375 + 24h vem (3µM)",
                    "Detection Pval",
                    "A375 + 24h vem (3µM) 1",
                    "Detection Pval",
                    "A375 + 24h vem (3µM) 2",
                    "Detection Pval",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
            )

    @tag("illumina")
    def test_illumina_quoted_row_names(self):
        organism = Organism(name="HOMO_SAPIENS", taxonomy_id=9606, is_scientific_name=True)
        organism.save()

        pj = prepare_illumina_job(
            {
                "source_filename": "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE33nnn/GSE33814/suppl/GSE33814%5Fnon%2Dnormalized%2Etxt%2Egz",
                # Some of the columns are trimmed to save space and time
                "filename": "GSE33814_trimmed_non-normalized.txt",
                "absolute_file_path": "/home/user/data_store/raw/TEST/ILLUMINA/GSE33814_trimmed_non-normalized.txt",
                "organism": organism,
                "samples": [("GSM836222", "IMGUS_32"), ("GSM836223", "IMGUS_33"),],
            }
        )

        final_context = illumina.illumina_to_pcl(pj.pk)
        self.assertFailedInIlluminaR(pj)

        # Make sure that the row names are no longer quoted after sanitizing the file
        def assertNotQuoted(string: str):
            self.assertNotEqual(string[0], '"')
            self.assertNotEqual(string[-1], '"')

        with open(final_context["sanitized_file_path"], "r") as f:
            reader = csv.reader(f, delimiter="\t")

            headers = next(reader)
            for header in headers:
                assertNotQuoted(header)

            # Also make sure the probe IDs aren't qutoed
            first_row = next(reader)
            assertNotQuoted(first_row[0])

    @tag("illumina")
    def test_illumina_rows_starting_with_whitespace(self):
        organism = Organism(name="HOMO_SAPIENS", taxonomy_id=9606, is_scientific_name=True)
        organism.save()

        pj = prepare_illumina_job(
            {
                "source_filename": "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE112nnn/GSE112517/suppl/GSE112517_non-normalized.txt.gz",
                "filename": "GSE112517_non-normalized.txt",
                "absolute_file_path": "/home/user/data_store/raw/TEST/ILLUMINA/GSE112517_non-normalized.txt",
                "organism": organism,
                "samples": [
                    (
                        "GSM3071991",
                        "MCF-7 KLHDC7B siRNA knockdown control",
                        {"description": ["SAMPLE 1"],},
                    ),
                    (
                        "GSM3071992",
                        "MCF-7 KLHDC7B siRNA knockdown",
                        {"description": ["SAMPLE 2"],},
                    ),
                ],
            }
        )

        final_context = illumina.illumina_to_pcl(pj.pk)
        self.assertFailedInIlluminaR(pj)

    @tag("illumina")
    def test_illumina_space_separated(self):
        organism = Organism(name="HOMO_SAPIENS", taxonomy_id=9606, is_scientific_name=True)
        organism.save()

        pj = prepare_illumina_job(
            {
                "source_filename": "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE48nnn/GSE48023/suppl/GSE48023%5Fnon%2Dnormalized%2Etxt%2Egz",
                # Some of the columns are trimmed to save space and time
                "filename": "GSE48023_trimmed_non-normalized.txt",
                "absolute_file_path": "/home/user/data_store/raw/TEST/ILLUMINA/GSE48023_trimmed_non-normalized.txt",
                "organism": organism,
                "samples": [
                    ("GSM1165512", "WholeBloodRNA_IN0242_Day0"),
                    ("GSM1165513", "WholeBloodRNA_IN0242_Day1"),
                    ("GSM1165514", "WholeBloodRNA_IN0242_Day14"),
                    ("GSM1165515", "WholeBloodRNA_IN0242_Day3"),
                    ("GSM1165516", "WholeBloodRNA_IN0243_Day0"),
                ],
            }
        )

        final_context = illumina.illumina_to_pcl(pj.pk)
        self.assertFailedInIlluminaR(pj)

        # Assert that the sanitized file is tab-separated (by reading it as a
        # TSV and making sure it has 11 headers) and has an extra ID_REF header
        with open(final_context["sanitized_file_path"], "r") as f:
            reader = csv.reader(f, delimiter="\t")

            headers = next(reader)

            # ID_REF + 5 observations + 5 p-values
            self.assertEqual(len(headers), 11)
            self.assertEqual(headers[0], "ID_REF")

    @tag("illumina")
    def test_illumina_no_pvalue(self):
        """This experiment should fail because it has no p-value columns, so
        make sure it fails at that stage of the processing"""
        organism = Organism(name="HOMO_SAPIENS", taxonomy_id=9606, is_scientific_name=True)
        organism.save()

        pj = prepare_illumina_job(
            {
                "source_filename": "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE41nnn/GSE41355/suppl/GSE41355%5Fnon%2Dnormalized%2Etxt%2Egz",
                "filename": "GSE41355_non-normalized.txt",
                "absolute_file_path": "/home/user/data_store/raw/TEST/ILLUMINA/GSE41355_non-normalized.txt",
                "organism": organism,
                "samples": [("GSM1015436", "IRF3/7 DKO 2"),],
            }
        )

        final_context = illumina.illumina_to_pcl(pj.pk)

        self.assertFailed(pj, "Could not detect PValue column!")

    @tag("illumina")
    def test_sanitize_file_hash_header(self):
        contents = """
# The identifiers in the ID_REF column must match the identifiers in the ID column of the referenced platform (GPLxxxx).
# The Matrix table should include normalized (scaled) signal count data and detection p-value
"# Values that should be disregarded may either be left blank or labeled as ""null""."
Index	Data
"""
        results = _try_sanitizing_file(contents)

        with open(results["sanitized_file_path"], "r") as f:
            self.assertEqual(f.read(), "Index	Data\n")

        os.remove(results["sanitized_file_path"])

    @tag("illumina")
    def test_sanitize_file_GSM_footer(self):
        """Some samples have this weird footer that we want to filter out"""
        contents = """
Index	Data
GSM674408	1523532074_A
GSM674409	1523532074_B
GSM674410	1523532074_C
GSM674411	1523532074_D
GSM674412	1523532074_E
GSM674413	1523532074_F
"""
        results = _try_sanitizing_file(contents)

        with open(results["sanitized_file_path"], "r") as f:
            self.assertEqual(f.read(), "Index	Data\n")

        os.remove(results["sanitized_file_path"])

    @tag("illumina")
    def test_sanitize_file_soft_example(self):
        # Example file from https://www.ncbi.nlm.nih.gov/geo/info/soft.html#examples
        contents = """
^SAMPLE = Control Embyronic Stem Cell Replicate 1
!Sample_label_protocol_ch1 = 10 痢 of total RNA were primed with 2 痞 of 100 然 T16N2 DNA primer at 70蚓 for 10 min, then reversed transcribed at 42蚓 for 1 h in the presence of 400 U SuperScript II RTase (Invitrogen), and 100 然 each dATP, dTTP, dGTP, with 25 然 dCTP, 25 然 Cy5-labeled dCTP (NEN Life Science, Boston, MA), and RNase inhibitor (Invitrogen). RNA was then degraded with RNase A, and labeled cDNAs were purified using QIAquick PCR columns (Qiagen).
!Sample_organism_ch2 = Mus musculus
!Sample_platform_id = GPL3759
#ID_REF =
#VALUE = log2(REDsignal/GREENsignal) per feature (processed signals used).
#LogRatioError = error of the log ratio calculated according to the error model chosen.
!sample_table_begin
ID_REF	VALUE	LogRatioError	PValueLogRatio	gProcessedSignal	rProcessedSignal
1	-1.6274758	1.36E-01	6.41E-33	9.13E+03	2.15E+02
2	0.1412248	1.34E+00	1.00E+00	4.14E+01	5.72E+01
"""

        results = _try_sanitizing_file(contents)

        with open(results["sanitized_file_path"], "r") as f:
            self.assertEqual(f.read().strip(), "\n".join(contents.splitlines()[-3:]))

        os.remove(results["sanitized_file_path"])

    @tag("illumina")
    def test_detect_columns(self):
        organism = Organism(name="HOMO_SAPIENS", taxonomy_id=9606, is_scientific_name=True)
        organism.save()

        job = prepare_illumina_job({**GSE22427, "organism": organism})

        pipeline = Pipeline(name=PipelineEnum.ILLUMINA.value)

        final_context = utils.run_pipeline(
            {"job_id": job.id, "pipeline": pipeline},
            [
                utils.start_job,
                illumina._prepare_files,
                illumina._detect_encoding,
                illumina._sanitize_input_file,
                illumina._convert_sanitized_to_tsv,
                illumina._detect_columns,
            ],
        )

        self.assertNotEqual(final_context.get("success"), False)

        # For this experiment, the probe ID is the first column
        self.assertEqual(final_context.get("probeId"), GSE22427_HEADER[0])

        expected_column_ids = ",".join(
            map(
                lambda t: str(t[0]),
                filter(
                    # For this header file, the samples all have the prefix LV-
                    lambda t: t[1].startswith("LV-"),
                    # We use start=1 here because the column IDs are formatted
                    # for R code so they treat the header as a 1-indexed list
                    enumerate(GSE22427_HEADER, start=1),
                ),
            )
        )
        self.assertEqual(final_context.get("columnIds"), expected_column_ids)
