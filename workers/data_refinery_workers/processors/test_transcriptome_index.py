import os
import shutil
from django.test import TestCase, tag
from unittest.mock import patch
from data_refinery_common.models import (
    SurveyJob,
    Organism,
    Sample,
    OriginalFile,
    OriginalFileSampleAssociation,
    ProcessorJobOriginalFileAssociation,
    ProcessorJob
)
from data_refinery_workers.processors import transcriptome_index, utils

def prepare_job():
    pj = ProcessorJob()
    pj.pipeline_applied = "TRANSCRIPTOME_INDEX_SHORT"
    pj.save()

    homo_sapiens = Organism.get_object_for_name("HOMO_SAPIENS")

    samp = Sample()
    samp.organism = homo_sapiens
    samp.save()

    og_file = OriginalFile()
    og_file.source_filename = "aegilops_tauschii_short.fa.gz"
    og_file.filename = "aegilops_tauschii_short.fa.gz"
    og_file.absolute_file_path = "/home/user/data_store/raw/TEST/TRANSCRIPTOME_INDEX/aegilops_tauschii_short.fa.gz"
    og_file.save()

    og_file2 = OriginalFile()
    og_file2.source_filename = "aegilops_tauschii_short.gtf.gz"
    og_file2.filename = "aegilops_tauschii_short.gtf.gz"
    og_file2.absolute_file_path = "/home/user/data_store/raw/TEST/TRANSCRIPTOME_INDEX/aegilops_tauschii_short.gtf.gz"
    og_file2.save()

    og_file_samp_assoc = OriginalFileSampleAssociation()
    og_file_samp_assoc.original_file = og_file
    og_file_samp_assoc.sample = samp
    og_file_samp_assoc.save()

    og_file_samp_assoc2 = OriginalFileSampleAssociation()
    og_file_samp_assoc2.original_file = og_file2
    og_file_samp_assoc2.sample = samp
    og_file_samp_assoc2.save()

    assoc1 = ProcessorJobOriginalFileAssociation()
    assoc1.original_file = og_file
    assoc1.processor_job = pj
    assoc1.save()

    assoc2 = ProcessorJobOriginalFileAssociation()
    assoc2.original_file = og_file2
    assoc2.processor_job = pj
    assoc2.save()


    return pj

class TXTestCase(TestCase):

    @tag('transcriptome')
    def test_tx(self):
        """ """
        job = prepare_job()
        transcriptome_index.build_transcriptome_index(job.pk)
