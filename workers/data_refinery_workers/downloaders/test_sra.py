from urllib.error import URLError
from typing import List
from unittest.mock import patch, call
from django.test import TestCase
from data_refinery_common.models import (
    SurveyJob,
    DownloaderJob,
    ProcessorJob
)
from data_refinery_workers.downloaders import sra
from data_refinery_common.job_lookup import ProcessorPipeline


class DownloadSraTestCase(TestCase):
    def setUp(self):
        survey_job = SurveyJob(source_type="SRA")
        survey_job.save()
        self.survey_job = survey_job

    def insert_objects(self):
        return 

    # @patch("os.path.getsize")
    # @patch("data_refinery_workers.downloaders.utils.send_job")
    # @patch("data_refinery_workers.downloaders.sra._download_file")
    # def test_happy_path(self,
    #                     mock_send_job,
    #                     mock_upload_raw_file,
    #                     mock_getsize):
    #     mock_send_job.return_value = None

    #     # We don't actually want to download anything and we're
    #     # testing this function separately anyway.
    #     mock_download_file.return_value = True

    #     mock_getsize.return_value = 1337

    #     batch, files = self.insert_objects()
    #     downloader_job = DownloaderJob.create_job_and_relationships(
    #         batches=[batch], downloader_task="dummy")
    #     downloader_job.save()

    #     sra.download_sra(downloader_job.id)

    #     downloader_job.refresh_from_db()
    #     self.assertTrue(downloader_job.success)
    #     for file in files:
    #         file.refresh_from_db()
    #         self.assertEquals(file.size_in_bytes, 1337)

    #     processor_job = ProcessorJob.objects.get()

    #     target_path_template = "/home/user/data_store/temp/IlluminaHiSeq2000/SALMON/downloader_job_{}/DRR002116_{}.fastq.gz"  # noqa
    #     target_path_1 = target_path_template.format(downloader_job.id, 1)
    #     target_path_2 = target_path_template.format(downloader_job.id, 2)

    #     # Impossible to match the exact File and DownloaderJob
    #     # objects, so rather than trying to do so, just pull them out
    #     # from the calls and test the path it was called with:
    #     first_call = mock_download_file.call_args_list[0][0]
    #     second_call = mock_download_file.call_args_list[1][0]
    #     mock_download_file.assert_has_calls([
    #         call(first_call[0], first_call[1], target_path_2),
    #         call(second_call[0], second_call[1], target_path_1)
    #     ])

    #     mock_send_job.assert_called_once_with(ProcessorPipeline.SALMON, processor_job.id)

    #     self.assertEquals(len(mock_upload_raw_file.mock_calls), 2)

    # def test_aspera_downloader(self):
    #     """ """

    #     batch = Batch(
    #         survey_job=self.survey_job,
    #         source_type="SRA",
    #         pipeline_required="SALMON",
    #         platform_accession_code="IlluminaHiSeq2000",
    #         experiment_accession_code="DRX001563",
    #         experiment_title="It doesn't really matter.",
    #         organism_id=9031,
    #         organism_name="GALLUS GALLUS",
    #         release_date="2013-07-19",
    #         last_uploaded_date="2017-09-11",
    #         status=BatchStatuses.NEW.value
    #     )
    #     batch.save()

    #     # This is converted from FTP URL to use Aspera
    #     file = File(size_in_bytes=0,
    #                 download_url="ftp://ftp.sra.ebi.ac.uk/vol1/fastq/ERR036/ERR036000/ERR036000_1.fastq.gz",  # noqa
    #                 raw_format="fastq.gz",
    #                 processed_format="tar.gz",
    #                 name="ERR036000_1.fastq.gz",
    #                 internal_location="IlluminaHiSeq2000/SALMON",
    #                 batch=batch)
    #     dj = DownloaderJob()

    #     self.assertTrue(sra._download_file(file,  dj, file.name))
