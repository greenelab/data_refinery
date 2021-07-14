import random
import re
import xml.etree.ElementTree as ET
from typing import Dict, List

from django.utils.dateparse import parse_date

from data_refinery_common.enums import Downloaders
from data_refinery_common.logging import get_and_configure_logger
from data_refinery_common.models import (
    Experiment,
    ExperimentAnnotation,
    ExperimentOrganismAssociation,
    ExperimentSampleAssociation,
    Organism,
    OriginalFile,
    OriginalFileSampleAssociation,
    Sample,
    SurveyJob,
)
from data_refinery_common.rna_seq import _build_ena_file_url
from data_refinery_common.utils import get_fasp_sra_download
from data_refinery_foreman.surveyor import harmony, utils
from data_refinery_foreman.surveyor.external_source import ExternalSourceSurveyor

logger = get_and_configure_logger(__name__)


DOWNLOAD_SOURCE = "NCBI"  # or "ENA". Change this to download from NCBI (US) or ENA (UK).
ENA_URL_TEMPLATE = "https://www.ebi.ac.uk/ena/data/view/{}"
ENA_METADATA_URL_TEMPLATE = "https://www.ebi.ac.uk/ena/data/view/{}&display=xml"
NCBI_DOWNLOAD_URL_TEMPLATE = (
    "anonftp@ftp.ncbi.nlm.nih.gov:/sra/sra-instant/reads/ByRun/sra/"
    "{first_three}/{first_six}/{accession}/{accession}.sra"
)
NCBI_PRIVATE_DOWNLOAD_URL_TEMPLATE = (
    "anonftp@ftp-private.ncbi.nlm.nih.gov:/sra/sra-instant/reads/ByRun/sra/"
    "{first_three}/{first_six}/{accession}/{accession}.sra"
)


class UnsupportedDataTypeError(Exception):
    pass


class SraSurveyor(ExternalSourceSurveyor):

    """Surveys SRA for data.

    Implements the ExternalSourceSurveyor interface. It is worth
    noting that a large part of this class is parsing XML metadata
    about Experiments. The strategy for parsing the metadata was to take
    nearly all of the fields that could be extracted from the
    XML. Essentially all the XML parsing code is just working through
    the XML in the way it is formatted. For reference see the sample
    XML which has been included in ./test_sra_xml.py. This sample XML
    is used for testing, so the values that are extracted from it can
    be found in ./test_sra.py.
    """

    def source_type(self):
        return Downloaders.SRA.value

    @staticmethod
    def gather_submission_metadata(metadata: Dict) -> None:

        formatted_metadata_URL = ENA_METADATA_URL_TEMPLATE.format(metadata["submission_accession"])
        response = utils.requests_retry_session().get(formatted_metadata_URL)
        submission_xml = ET.fromstring(response.text)[0]
        submission_metadata = submission_xml.attrib

        # We already have these
        submission_metadata.pop("accession", "")
        submission_metadata.pop("alias", "")

        metadata.update(submission_metadata)

        for child in submission_xml:
            if child.tag == "TITLE":
                metadata["submission_title"] = child.text
            elif child.tag == "SUBMISSION_ATTRIBUTES":
                for grandchild in child:
                    metadata[grandchild.find("TAG").text.lower()] = grandchild.find("VALUE").text

    @staticmethod
    def gather_library_metadata(metadata: Dict, library: ET.Element) -> None:
        for child in library:
            if child.tag == "LIBRARY_LAYOUT":
                metadata["library_layout"] = child[0].tag
            else:
                metadata[child.tag.lower()] = child.text

        # SRA contains several types of data. We only want RNA-Seq for now.
        if metadata["library_strategy"] != "RNA-Seq":
            raise UnsupportedDataTypeError("library_strategy not RNA-Seq.")
        if metadata["library_source"] not in ["TRANSCRIPTOMIC", "OTHER"]:
            raise UnsupportedDataTypeError(
                "library_source: " + metadata["library_source"] + " not TRANSCRIPTOMIC or OTHER."
            )

    @staticmethod
    def parse_read_spec(metadata: Dict, read_spec: ET.Element, counter: int) -> None:
        for child in read_spec:
            key = "read_spec_{}_{}".format(str(counter), child.tag.replace("READ_", "").lower())
            metadata[key] = child.text

    @staticmethod
    def gather_spot_metadata(metadata: Dict, spot: ET.Element) -> None:
        """We don't actually know what this metadata is about.

        SPOT_DECODE_SPECs have a variable number of READ_SPECs so we
        have to add a counter to the keys in order to make them work
        within the flat dictionary we're building.
        """
        for child in spot:
            if child.tag == "SPOT_DECODE_SPEC":
                read_spec_counter = 0
                for grandchild in child:
                    if grandchild.tag == "SPOT_LENGTH":
                        metadata["spot_length"] = grandchild.text
                    elif grandchild.tag == "READ_SPEC":
                        SraSurveyor.parse_read_spec(metadata, grandchild, read_spec_counter)
                        read_spec_counter = read_spec_counter + 1

    @staticmethod
    def gather_experiment_metadata(metadata: Dict) -> None:
        formatted_metadata_URL = ENA_METADATA_URL_TEMPLATE.format(metadata["experiment_accession"])
        response = utils.requests_retry_session().get(formatted_metadata_URL)
        experiment_xml = ET.fromstring(response.text)

        experiment = experiment_xml[0]
        for child in experiment:
            if child.tag == "TITLE":
                metadata["experiment_title"] = child.text
            elif child.tag == "DESIGN":
                for grandchild in child:
                    if grandchild.tag == "DESIGN_DESCRIPTION":
                        metadata["experiment_design_description"] = grandchild.text
                    elif grandchild.tag == "LIBRARY_DESCRIPTOR":
                        SraSurveyor.gather_library_metadata(metadata, grandchild)
                    elif grandchild.tag == "SPOT_DESCRIPTOR":
                        SraSurveyor.gather_spot_metadata(metadata, grandchild)
            elif child.tag == "PLATFORM":
                # This structure is extraneously nested.
                metadata["platform_instrument_model"] = child[0][0].text

    @staticmethod
    def parse_run_link(run_link: ET.ElementTree) -> (str, str):
        key = ""
        value = ""

        # The first level in this element is a XREF_LINK which is
        # really just an unnecessary level
        for child in run_link[0]:
            if child.tag == "DB":
                # The key is prefixed with "ena-" which is unnecessary
                # since we know this is coming from ENA
                key = child.text.lower().replace("ena-", "") + "_accession"
            elif child.tag == "ID":
                value = child.text

        return (key, value)

    @staticmethod
    def parse_attribute(attribute: ET.ElementTree, key_prefix: str = "") -> (str, str):
        """Parse an XML attribute.

        Takes an optional key_prefix which is used to differentiate
        keys which may be shared between different object types. For
        example "title" could be the experiment_title or the
        sample_title.
        """
        key = ""
        value = ""

        for child in attribute:
            if child.tag == "TAG":
                key = key_prefix + child.text.lower().replace("-", "_").replace(" ", "_")
            elif child.tag == "VALUE":
                value = child.text

        return (key, value)

    @staticmethod
    def gather_run_metadata(run_accession: str) -> Dict:
        """A run refers to a specific read in an experiment."""

        discoverable_accessions = ["study_accession", "sample_accession", "submission_accession"]

        response = utils.requests_retry_session().get(
            ENA_METADATA_URL_TEMPLATE.format(run_accession)
        )
        try:
            run_xml = ET.fromstring(response.text)
        except Exception:
            logger.exception("Unable to decode response", response=response.text)
            return {}

        # Necessary because ERP000263 has only one ROOT element containing this error:
        # Entry: ERR15562 display type is either not supported or entry is not found.
        if len(run_xml) == 0:
            return {}

        run_item = run_xml[0]

        useful_attributes = ["center_name", "run_center", "run_date", "broker_name", "alias"]
        metadata = {}
        for attribute in useful_attributes:
            if attribute in run_item.attrib:
                metadata[attribute] = run_item.attrib[attribute]
        metadata["run_accession"] = run_accession

        for child in run_item:
            if child.tag == "EXPERIMENT_REF":
                metadata["experiment_accession"] = child.attrib["accession"]
            elif child.tag == "RUN_LINKS":
                for grandchild in child:
                    key, value = SraSurveyor.parse_run_link(grandchild)
                    if value != "" and key in discoverable_accessions:
                        metadata[key] = value
            elif child.tag == "RUN_ATTRIBUTES":
                for grandchild in child:
                    key, value = SraSurveyor.parse_attribute(grandchild, "run_")
                    metadata[key] = value

        return metadata

    @staticmethod
    def gather_sample_metadata(metadata: Dict) -> None:
        formatted_metadata_URL = ENA_METADATA_URL_TEMPLATE.format(metadata["sample_accession"])
        response = utils.requests_retry_session().get(formatted_metadata_URL)
        sample_xml = ET.fromstring(response.text)

        sample = sample_xml[0]

        if "center_name" in sample.attrib:
            metadata["sample_center_name"] = sample.attrib["center_name"]

        for child in sample:
            if child.tag == "TITLE":
                metadata["sample_title"] = child.text
            elif child.tag == "SAMPLE_NAME":
                for grandchild in child:
                    if grandchild.tag == "TAXON_ID":
                        metadata["organism_id"] = grandchild.text
                    elif grandchild.tag == "SCIENTIFIC_NAME":
                        metadata["organism_name"] = grandchild.text.upper()
            elif child.tag == "SAMPLE_ATTRIBUTES":
                for grandchild in child:
                    key, value = SraSurveyor.parse_attribute(grandchild, "sample_")
                    metadata[key] = value

    @staticmethod
    def gather_study_metadata(metadata: Dict) -> None:
        formatted_metadata_URL = ENA_METADATA_URL_TEMPLATE.format(metadata["study_accession"])
        response = utils.requests_retry_session().get(formatted_metadata_URL)
        study_xml = ET.fromstring(response.text)

        study = study_xml[0]
        for child in study:
            if child.tag == "DESCRIPTOR":
                for grandchild in child:
                    # STUDY_TYPE is the only tag which uses attributes
                    # instead of the text for whatever reason
                    if grandchild.tag == "STUDY_TYPE":
                        metadata[grandchild.tag.lower()] = grandchild.attrib["existing_study_type"]
                    else:
                        metadata[grandchild.tag.lower()] = grandchild.text
            elif child.tag == "STUDY_ATTRIBUTES":
                for grandchild in child:
                    key, value = SraSurveyor.parse_attribute(grandchild, "study_")
                    metadata[key] = value
            elif child.tag == "STUDY_LINKS":
                for grandchild in child:
                    for ggc in grandchild:
                        if ggc.getchildren()[0].text == "pubmed":
                            metadata["pubmed_id"] = ggc.getchildren()[1].text
                            break
            elif child.tag == "IDENTIFIERS":
                for grandchild in child:
                    if (
                        # Check for GEO accessions. These live inside an
                        # EXTERNAL_ID tag with namespace GEO
                        grandchild.tag == "EXTERNAL_ID"
                        and grandchild.attrib.get("namespace", "") == "GEO"
                        and re.match(r"^GSE\d{2,6}", grandchild.text)
                    ) or (
                        # Check for ArrayExpress accessions. These live inside a
                        # SUBMITTER_ID tag, but the namespace is not standardized
                        grandchild.tag == "SUBMITTER_ID"
                        and re.match(r"^E-[A-Z]{4}-\d{2,6}", grandchild.text)
                    ):
                        metadata["external_id"] = grandchild.text
                        break

    @staticmethod
    def gather_all_metadata(run_accession):
        metadata = SraSurveyor.gather_run_metadata(run_accession)

        if metadata != {}:
            SraSurveyor.gather_experiment_metadata(metadata)
            SraSurveyor.gather_sample_metadata(metadata)
            SraSurveyor.gather_study_metadata(metadata)
            SraSurveyor.gather_submission_metadata(metadata)

        return metadata

    @staticmethod
    def _build_ncbi_file_url(run_accession: str):
        """ Build the path to the hypothetical .sra file we want """
        accession = run_accession
        first_three = accession[:3]
        first_six = accession[:6]

        # Prefer the FASP-specific endpoints if possible..
        download_url = get_fasp_sra_download(run_accession)

        if not download_url:
            # ..else, load balancing via coin flip.
            if random.choice([True, False]):
                download_url = NCBI_DOWNLOAD_URL_TEMPLATE.format(
                    first_three=first_three, first_six=first_six, accession=accession
                )
            else:
                download_url = NCBI_PRIVATE_DOWNLOAD_URL_TEMPLATE.format(
                    first_three=first_three, first_six=first_six, accession=accession
                )

        return download_url

    @staticmethod
    def _apply_harmonized_metadata_to_sample(sample: Sample, metadata: dict):
        """Harmonizes the metadata and applies it to `sample`"""
        harmonizer = harmony.Harmonizer()
        harmonized_sample = harmonizer.harmonize_sample(metadata)
        for key, value in harmonized_sample.items():
            setattr(sample, key, value)

    @staticmethod
    def _apply_metadata_to_experiment(experiment: Experiment, metadata: dict):
        experiment.source_url = ENA_URL_TEMPLATE.format(experiment.accession_code)
        experiment.source_database = "SRA"
        experiment.technology = "RNA-SEQ"

        # We don't get this value from the API, unfortunately.
        # experiment.platform_accession_code = experiment["platform_accession_code"]

        if not experiment.description:
            experiment.description = "No description."

        if "study_title" in metadata:
            experiment.title = metadata["study_title"]
        if "study_abstract" in metadata:
            experiment.description = metadata["study_abstract"]
        if "lab_name" in metadata:
            experiment.submitter_institution = metadata["lab_name"]
        if "experiment_design_description" in metadata:
            experiment.protocol_description = metadata["experiment_design_description"]
        if "pubmed_id" in metadata:
            experiment.pubmed_id = metadata["pubmed_id"]
            experiment.has_publication = True
        if "study_ena_first_public" in metadata:
            experiment.source_first_published = parse_date(metadata["study_ena_first_public"])
        if "study_ena_last_update" in metadata:
            experiment.source_last_modified = parse_date(metadata["study_ena_last_update"])

        if metadata.get("external_id", None) is not None:
            experiment.alternate_accession_code = metadata["external_id"]

        # Rare, but it happens.
        if not experiment.protocol_description:
            # metadata.get() doesn't work here because sometimes the
            # key is present but its value is None, in which case None
            # is returned, causing our database constraint to be
            # violated.
            if (
                "library_construction_protocol" in metadata
                and metadata["library_construction_protocol"]
            ):
                experiment.protocol_description = metadata["library_construction_protocol"]
            else:
                experiment.protocol_description = "Protocol was never provided."

        # Scrape publication title and authorship from Pubmed
        if experiment.pubmed_id:
            pubmed_metadata = utils.get_title_and_authors_for_pubmed_id(experiment.pubmed_id)
            experiment.publication_title = pubmed_metadata[0]
            experiment.publication_authors = pubmed_metadata[1]

    def _generate_experiment_and_samples(
        self, run_accession: str, study_accession: str = None
    ) -> (Experiment, List[Sample]):
        """Generates Experiments and Samples for the provided run_accession."""
        metadata = SraSurveyor.gather_all_metadata(run_accession)

        if metadata == {}:
            if study_accession:
                logger.error(
                    "Could not discover any metadata for run.",
                    accession=run_accession,
                    study_accession=study_accession,
                )
            else:
                logger.error("Could not discover any metadata for run.", accession=run_accession)
            return (None, None)  # This will cascade properly

        if DOWNLOAD_SOURCE == "ENA":
            if metadata["library_layout"] == "PAIRED":
                files_urls = [
                    _build_ena_file_url(run_accession, "_1"),
                    _build_ena_file_url(run_accession, "_2"),
                ]
            else:
                files_urls = [_build_ena_file_url(run_accession)]
        else:
            files_urls = [SraSurveyor._build_ncbi_file_url(run_accession)]

        # Figure out the Organism for this sample
        organism_name = metadata.pop("organism_name", None)
        if not organism_name:
            logger.error("Could not discover organism type for run.", accession=run_accession)
            return (None, None)  # This will cascade properly

        organism_name = organism_name.upper()
        organism = Organism.get_object_for_name(organism_name)

        ##
        # Experiment
        ##

        experiment_accession_code = metadata.get("study_accession")
        try:
            experiment_object = Experiment.objects.get(accession_code=experiment_accession_code)
            logger.debug(
                "Experiment already exists, skipping object creation.",
                experiment_accession_code=experiment_accession_code,
                survey_job=self.survey_job.id,
            )
        except Experiment.DoesNotExist:
            experiment_object = Experiment()
            experiment_object.accession_code = experiment_accession_code
            SraSurveyor._apply_metadata_to_experiment(experiment_object, metadata)
            experiment_object.save()

            ##
            # Experiment Metadata
            ##
            json_xa = ExperimentAnnotation()
            json_xa.experiment = experiment_object
            json_xa.data = metadata
            json_xa.is_ccdl = False
            json_xa.save()

        ##
        # Samples
        ##

        sample_accession_code = metadata.pop("run_accession")
        # Create the sample object
        try:
            sample_object = Sample.objects.get(accession_code=sample_accession_code)
            # If current experiment includes new protocol information,
            # merge it into the sample's existing protocol_info.
            protocol_info, is_updated = self.update_sample_protocol_info(
                sample_object.protocol_info,
                experiment_object.protocol_description,
                experiment_object.source_url,
            )
            if is_updated:
                sample_object.protocol_info = protocol_info
                sample_object.save()

            logger.debug(
                "Sample %s already exists, skipping object creation.",
                sample_accession_code,
                experiment_accession_code=experiment_object.accession_code,
                survey_job=self.survey_job.id,
            )
        except Sample.DoesNotExist:
            sample_object = Sample()
            sample_object.source_database = "SRA"
            sample_object.accession_code = sample_accession_code
            sample_object.organism = organism

            sample_object.platform_name = metadata.get("platform_instrument_model", "UNKNOWN")
            # The platform_name is human readable and contains spaces,
            # accession codes shouldn't have spaces though:
            sample_object.platform_accession_code = sample_object.platform_name.replace(" ", "")
            sample_object.technology = "RNA-SEQ"
            if (
                "ILLUMINA" in sample_object.platform_name.upper()
                or "NEXTSEQ" in sample_object.platform_name.upper()
            ):
                sample_object.manufacturer = "ILLUMINA"
            elif "ION TORRENT" in sample_object.platform_name.upper():
                sample_object.manufacturer = "ION_TORRENT"
            else:
                sample_object.manufacturer = "UNKNOWN"

            SraSurveyor._apply_harmonized_metadata_to_sample(sample_object, metadata)

            protocol_info, is_updated = self.update_sample_protocol_info(
                existing_protocols=[],
                experiment_protocol=experiment_object.protocol_description,
                experiment_url=experiment_object.source_url,
            )
            # Do not check is_updated the first time because we must
            # save a list so we can append to it later.
            sample_object.protocol_info = protocol_info

            sample_object.save()

            for file_url in files_urls:
                original_file = OriginalFile.objects.get_or_create(
                    source_url=file_url, source_filename=file_url.split("/")[-1], has_raw=True
                )[0]
                OriginalFileSampleAssociation.objects.get_or_create(
                    original_file=original_file, sample=sample_object
                )

        # Create associations if they don't already exist
        ExperimentSampleAssociation.objects.get_or_create(
            experiment=experiment_object, sample=sample_object
        )

        ExperimentOrganismAssociation.objects.get_or_create(
            experiment=experiment_object, organism=organism
        )

        return experiment_object, [sample_object]

    @staticmethod
    def update_sample_protocol_info(existing_protocols, experiment_protocol, experiment_url):
        """Compares experiment_protocol with a sample's
        existing_protocols and update the latter if the former is new.

        Returns a tuple whose first element is existing_protocols (which
        may or may not have been updated) and the second is a boolean
        indicating whether exisiting_protocols has been updated.
        """

        if experiment_protocol == "Protocol was never provided.":
            return (existing_protocols, False)

        existing_descriptions = [protocol["Description"] for protocol in existing_protocols]
        if experiment_protocol in existing_descriptions:
            return (existing_protocols, False)

        existing_protocols.append({"Description": experiment_protocol, "Reference": experiment_url})
        return (existing_protocols, True)

    def discover_experiment_and_samples(self):
        """Returns an experiment and a list of samples for an SRA accession"""
        survey_job = SurveyJob.objects.get(id=self.survey_job.id)
        survey_job_properties = survey_job.get_properties()
        accession = survey_job_properties["experiment_accession_code"]

        # SRA Surveyor is mainly designed for SRRs, this handles SRPs
        if "SRP" in accession or "ERP" in accession or "DRP" in accession:
            response = utils.requests_retry_session().get(
                ENA_METADATA_URL_TEMPLATE.format(accession)
            )

            # If the status code is 404, then SRA doesn't know about this accession
            if response.status_code == 404:
                return None, None

            experiment_xml = ET.fromstring(response.text)[0]
            study_links = experiment_xml[2]  # STUDY_LINKS

            accessions_to_run = []
            for child in study_links:
                if child[0][0].text == "ENA-RUN":

                    all_runs = child[0][1].text

                    # Ranges can be disjoint, separated by commas
                    run_segments = all_runs.split(",")
                    for segment in run_segments:
                        if "-" in segment:
                            start, end = segment.split("-")
                        else:
                            start = segment
                            end = segment
                        start_id = start[3::]
                        end_id = end[3::]

                        for run_id in range(int(start_id), int(end_id) + 1):
                            run_id = str(run_id).zfill(len(start_id))
                            accessions_to_run.append(accession[0] + "RR" + run_id)
                    break

            experiment = None
            all_samples = []
            for run_id in accessions_to_run:
                logger.debug(
                    "Surveying SRA Run Accession %s for Experiment %s",
                    run_id,
                    accession,
                    survey_job=self.survey_job.id,
                )

                returned_experiment, samples = self._generate_experiment_and_samples(
                    run_id, accession
                )

                # Some runs may return (None, None). If this happens
                # we don't want to set experiment to None.
                if returned_experiment:
                    experiment = returned_experiment

                if samples:
                    all_samples += samples

            # So we prevent duplicate downloads, ex for SRP111553
            all_samples = list(set(all_samples))

            # Experiment will always be the same
            return experiment, all_samples

        else:
            logger.debug("Surveying SRA Run Accession %s", accession, survey_job=self.survey_job.id)
            return self._generate_experiment_and_samples(accession)
