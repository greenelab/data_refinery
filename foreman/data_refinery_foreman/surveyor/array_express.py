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
    SampleAnnotation,
    SurveyJobKeyValue,
)
from data_refinery_common.utils import (
    get_normalized_platform,
    get_readable_affymetrix_names,
    get_supported_microarray_platforms,
)
from data_refinery_foreman.surveyor import harmony, utils
from data_refinery_foreman.surveyor.external_source import ExternalSourceSurveyor

logger = get_and_configure_logger(__name__)


EXPERIMENTS_URL = "https://www.ebi.ac.uk/arrayexpress/json/v3/experiments/"
SAMPLES_URL = EXPERIMENTS_URL + "{}/samples"
UNKNOWN = "UNKNOWN"


class UnsupportedPlatformException(Exception):
    pass


class ArrayExpressSurveyor(ExternalSourceSurveyor):
    def source_type(self):
        return Downloaders.ARRAY_EXPRESS.value

    @staticmethod
    def _get_last_update_date(parsed_json: Dict) -> str:
        if "lastupdatedate" in parsed_json:
            return parsed_json["lastupdatedate"]
        else:
            return parsed_json["releasedate"]

    @classmethod
    def _apply_metadata_to_experiment(cls, experiment_object: Experiment, parsed_json: Dict):
        # We aren't sure these fields will be populated, or how many there will be.
        # Try to join them all together, or set a sensible default.
        experiment_descripton = ""
        if "description" in parsed_json and len(parsed_json["description"]) > 0:
            for description_item in parsed_json["description"]:
                if "text" in description_item:
                    experiment_descripton = experiment_descripton + description_item["text"] + "\n"

        if experiment_descripton == "":
            experiment_descripton = "Description not available.\n"

        experiment_object.source_database = "ARRAY_EXPRESS"
        experiment_object.title = parsed_json["name"]
        # This will need to be updated if we ever use Array
        # Express to get other kinds of data.
        experiment_object.technology = "MICROARRAY"
        experiment_object.description = experiment_descripton

        experiment_object.source_first_published = parse_date(parsed_json["releasedate"])
        experiment_object.source_last_modified = parse_date(cls._get_last_update_date(parsed_json))

    def create_experiment_from_api(self, experiment_accession_code: str) -> (Experiment, Dict):
        """Given an experiment accession code, create an Experiment object.

        Also returns a dictionary of additional information about the
        platform discovered for the experiment.

        Will raise an UnsupportedPlatformException if this experiment was
        conducted using a platform which we don't support.

        See an example at: https://www.ebi.ac.uk/arrayexpress/json/v3/experiments/E-MTAB-3050/sample
        """
        request_url = EXPERIMENTS_URL + experiment_accession_code
        experiment_request = utils.requests_retry_session().get(request_url, timeout=60)

        try:
            parsed_json = experiment_request.json()["experiments"]["experiment"][0]
        except KeyError:
            logger.error(
                "Remote experiment has no Experiment data!",
                experiment_accession_code=experiment_accession_code,
                survey_job=self.survey_job.id,
            )
            raise

        experiment = {}
        experiment["name"] = parsed_json["name"]
        experiment["experiment_accession_code"] = experiment_accession_code

        # This experiment has no platform at all, and is therefore useless.
        if "arraydesign" not in parsed_json or len(parsed_json["arraydesign"]) == 0:
            logger.warn(
                "Remote experiment has no arraydesign listed.",
                experiment_accession_code=experiment_accession_code,
                survey_job=self.survey_job.id,
            )
            raise UnsupportedPlatformException
        # If there is more than one arraydesign listed in the experiment
        # then there is no other way to determine which array was used
        # for which sample other than looking at the header of the CEL
        # file. That obviously cannot happen until the CEL file has been
        # downloaded so we can just mark it as UNKNOWN and let the
        # downloader inspect the downloaded file to determine the
        # array then.
        elif (
            len(parsed_json["arraydesign"]) != 1 or "accession" not in parsed_json["arraydesign"][0]
        ):
            experiment["platform_accession_code"] = UNKNOWN
            experiment["platform_accession_name"] = UNKNOWN
            experiment["manufacturer"] = UNKNOWN
        else:
            external_accession = parsed_json["arraydesign"][0]["accession"]
            for platform in get_supported_microarray_platforms():
                if platform["external_accession"] == external_accession:
                    experiment["platform_accession_code"] = get_normalized_platform(
                        platform["platform_accession"]
                    )

                    # Illumina appears in the accession codes for
                    # platforms manufactured by Illumina
                    if "ILLUMINA" in experiment["platform_accession_code"].upper():
                        experiment["manufacturer"] = "ILLUMINA"
                        experiment["platform_accession_name"] = platform["platform_accession"]
                    else:
                        # It's not Illumina, the only other supported Microarray platform is
                        # Affy. As our list of supported platforms grows this logic will
                        # need to get more sophisticated.
                        experiment["manufacturer"] = "AFFYMETRIX"
                        platform_mapping = get_readable_affymetrix_names()
                        experiment["platform_accession_name"] = platform_mapping[
                            platform["platform_accession"]
                        ]

            if "platform_accession_code" not in experiment:
                # We don't know what platform this accession corresponds to.
                experiment["platform_accession_code"] = external_accession
                experiment["platform_accession_name"] = UNKNOWN
                experiment["manufacturer"] = UNKNOWN

        experiment["release_date"] = parsed_json["releasedate"]
        experiment["last_update_date"] = self._get_last_update_date(parsed_json)

        # Create the experiment object
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
            experiment_object.source_url = request_url
            ArrayExpressSurveyor._apply_metadata_to_experiment(experiment_object, parsed_json)
            experiment_object.save()

            json_xa = ExperimentAnnotation()
            json_xa.experiment = experiment_object
            json_xa.data = parsed_json
            json_xa.is_ccdl = False
            json_xa.save()

            # Fetch and parse the IDF/SDRF file for any other fields
            IDF_URL_TEMPLATE = "https://www.ebi.ac.uk/arrayexpress/files/{code}/{code}.idf.txt"
            idf_url = IDF_URL_TEMPLATE.format(code=experiment_accession_code)
            idf_text = utils.requests_retry_session().get(idf_url, timeout=60).text

            lines = idf_text.split("\n")
            idf_dict = {}
            for line in lines:
                keyval = line.strip().split("\t")
                if len(keyval) == 2:
                    idf_dict[keyval[0]] = keyval[1]
                elif len(keyval) > 2:
                    idf_dict[keyval[0]] = keyval[1:]

            idf_xa = ExperimentAnnotation()
            idf_xa.data = idf_dict
            idf_xa.experiment = experiment_object
            idf_xa.is_ccdl = False
            idf_xa.save()

            if "Investigation Title" in idf_dict and isinstance(
                idf_dict["Investigation Title"], str
            ):
                experiment_object.title = idf_dict["Investigation Title"]
            if "Person Affiliation" in idf_dict:
                # This is very rare, ex: E-MEXP-32
                if isinstance(idf_dict["Person Affiliation"], list):

                    unique_people = list(set(idf_dict["Person Affiliation"]))
                    experiment_object.submitter_institution = ", ".join(unique_people)[:255]
                else:
                    experiment_object.submitter_institution = idf_dict["Person Affiliation"]

            # Get protocol_description from "<experiment_url>/protocols"
            # instead of from idf_dict, because the former provides more
            # details.
            protocol_url = request_url + "/protocols"
            protocol_request = utils.requests_retry_session().get(protocol_url, timeout=60)
            try:
                experiment_object.protocol_description = protocol_request.json()["protocols"]
            except KeyError:
                logger.warning(
                    "Remote experiment has no protocol data!",
                    experiment_accession_code=experiment_accession_code,
                    survey_job=self.survey_job.id,
                )

            if "Publication Title" in idf_dict:
                # This will happen for some superseries.
                # Ex: E-GEOD-29536
                # Assume most recent is "best:, store the rest in experiment annotation.
                if isinstance(idf_dict["Publication Title"], list):
                    experiment_object.publication_title = "; ".join(idf_dict["Publication Title"])
                else:
                    experiment_object.publication_title = idf_dict["Publication Title"]
                experiment_object.has_publication = True
            if "Publication DOI" in idf_dict:
                if isinstance(idf_dict["Publication DOI"], list):
                    experiment_object.publication_doi = ", ".join(idf_dict["Publication DOI"])
                else:
                    experiment_object.publication_doi = idf_dict["Publication DOI"]
                experiment_object.has_publication = True
            if "PubMed ID" in idf_dict:
                if isinstance(idf_dict["PubMed ID"], list):
                    experiment_object.pubmed_id = ", ".join(idf_dict["PubMed ID"])
                else:
                    experiment_object.pubmed_id = idf_dict["PubMed ID"]
                experiment_object.has_publication = True

            # Scrape publication title and authorship from Pubmed
            if experiment_object.pubmed_id:
                pubmed_metadata = utils.get_title_and_authors_for_pubmed_id(
                    experiment_object.pubmed_id
                )
                experiment_object.publication_title = pubmed_metadata[0]
                experiment_object.publication_authors = pubmed_metadata[1]

            experiment_object.save()

        platform_dict = {}
        for k in ("platform_accession_code", "platform_accession_name", "manufacturer"):
            platform_dict[k] = experiment[k]

        return experiment_object, platform_dict

    def determine_sample_accession(
        self,
        experiment_accession: str,
        sample_source_name: str,
        sample_assay_name: str,
        filename: str,
    ) -> str:
        """Determine what to use as the sample's accession code.

        This is a complicated heuristic to determine the sample
        accession because there isn't a field that consistently
        contains it so we're trying to figure out a heuristic that
        will work for all the data. This may need even further
        refinements if we come across examples that break it.

        However, what's going on is that we think either the `source`
        or `assay` field will be the sample accession but it's not
        always the same.
        Ex: E-MEXP-669 has it in sample_assay_name.
        Therefore we try a few different things to determine which it
        is.

        The experiment accession must be prefixed since accessions
        are non-unique on AE, ex "Sample 1" is a valid assay name.
        """

        # It SEEMS like the filename often contains part or all of the
        # sample name so we first try to see if either field contains
        # the filename with the extension stripped off:
        if isinstance(filename, str):
            stripped_filename = ".".join(filename.split(".")[:-1])
            if stripped_filename != "":
                if stripped_filename in sample_source_name:
                    return experiment_accession + "-" + sample_source_name
                elif stripped_filename in sample_assay_name:
                    return experiment_accession + "-" + sample_assay_name

        # Accessions don't have spaces in them, but sometimes these
        # fields do so next we try to see if one has spaces and the
        # other doesn't:
        source_has_spaces = " " in sample_source_name
        assay_has_spaces = " " in sample_assay_name
        if assay_has_spaces and not source_has_spaces:
            return experiment_accession + "-" + sample_source_name
        elif source_has_spaces and not assay_has_spaces:
            return experiment_accession + "-" + sample_assay_name

        # We're out of options so return the longest one.
        if len(sample_source_name) >= len(sample_assay_name):
            return experiment_accession + "-" + sample_source_name
        else:
            return experiment_accession + "-" + sample_assay_name

    @staticmethod
    def extract_protocol_text(protocol_text):
        """Returns a string representation of protocol_text.

        protocol_text may be a string or a list containing both
        strings and dicts, like so (it's what the API returns
        sometimes, see E-MEXP-2381 as an example):
        [
          "Microarrays were imaged using an Agilent microarray scanner in XDR (eXtended Dynamic Range function) mode and a scan resolution of 5 \u00b5m.",
          {
            "br": null
          },
          "(Parameters: Scanning hardware = DNA Microarray Scanner BA [Agilent Technologies], Scanning software = Feature Extraction Software [Agilent])"
        ]
        """
        if not protocol_text:
            return ""
        elif type(protocol_text) == str:
            return protocol_text.strip()
        elif type(protocol_text) == list:
            # These can be {"br": None}, so skip non string lines
            return " ".join([line.strip() for line in protocol_text if type(line) == str])
        else:
            # Not sure what would get us here, but it's not worth raising an error over
            return str(protocol_text)

    @staticmethod
    def update_sample_protocol_info(existing_protocols, experiment_protocol, protocol_url):
        """Compares experiment_protocol with a sample's
        existing_protocols and updates the latter if the former includes
        any new entry.

        Returns a two-element tuple, the first is existing_protocols
        (which may or may not have been updated) and the second is a
        bool indicating whether exisiting_protocols has been updated.

        Note that the ArrayExpress experiment-level protocol may include
        multiple protocol entries.
        """

        if not "protocol" in experiment_protocol:
            return (existing_protocols, False)

        is_updated = False
        # Compare each entry in experiment protocol with the existing
        # protocols; if the entry is new, add it to exising_protocols.
        for new_protocol in experiment_protocol["protocol"]:
            new_protocol_text = new_protocol.get("text", "")
            new_protocol_text = ArrayExpressSurveyor.extract_protocol_text(new_protocol_text)

            # Ignore experiment-level protocols whose accession or text
            # field is unavailable or empty.
            if not new_protocol.get("accession", "").strip() or not new_protocol_text:
                continue

            new_protocol_is_found = False
            for existing_protocol in existing_protocols:
                if (
                    new_protocol.get("accession", "") == existing_protocol["Accession"]
                    and new_protocol_text == existing_protocol["Text"]
                    and new_protocol.get("type", "") == existing_protocol["Type"]
                ):
                    new_protocol_is_found = True
                    break
            if not new_protocol_is_found:
                existing_protocols.append(
                    {
                        "Accession": new_protocol["accession"],
                        "Text": new_protocol_text,
                        "Type": new_protocol.get("type", ""),  # in case 'type' field is unavailable
                        "Reference": protocol_url,
                    }
                )
                is_updated = True

        return (existing_protocols, is_updated)

    @staticmethod
    def _apply_harmonized_metadata_to_sample(sample: Sample, harmonized_metadata: dict):
        """Applies the harmonized metadata to `sample`"""
        for key, value in harmonized_metadata.items():
            setattr(sample, key, value)

    def create_samples_from_api(self, experiment: Experiment, platform_dict: Dict) -> List[Sample]:
        """Generates a Sample item for each sample in an AE experiment.

        There are many possible data situations for a sample:

            - If the sample only has raw data available:
                - If it is on a platform that we support:
                    Download this raw data and process it
                - If it is not on a platform we support:
                    Don't download anything, don't process anything
            - If the sample has both raw and derived data:
                - If the raw data is on a platform we support:
                    Download the raw data and process it, abandon the derived data
                - If the raw data is not on a platform we support
                    Download the derived data and no-op it, abandon the raw data
            - If the sample only has derived data:
                Download the derived data and no-op it.

        See an example at: https://www.ebi.ac.uk/arrayexpress/json/v3/experiments/E-MTAB-3050/samples
        """

        created_samples = []

        samples_endpoint = SAMPLES_URL.format(experiment.accession_code)
        r = utils.requests_retry_session().get(samples_endpoint, timeout=60)
        samples = r.json()["experiment"]["sample"]

        # The SDRF is the complete metadata record on a sample/property basis.
        # We run this through our harmonizer and then attach the properties
        # to our created samples.
        SDRF_URL_TEMPLATE = "https://www.ebi.ac.uk/arrayexpress/files/{code}/{code}.sdrf.txt"
        sdrf_url = SDRF_URL_TEMPLATE.format(code=experiment.accession_code)
        sdrf_samples = harmony.parse_sdrf(sdrf_url)

        title_field = harmony.determine_title_field(sdrf_samples, samples)
        harmonized_samples = harmony.harmonize_all_samples(sdrf_samples, title_field)

        # An experiment can have many samples
        for sample_data in samples:

            # For some reason, this sample has no files associated with it.
            if "file" not in sample_data or len(sample_data["file"]) == 0:
                continue

            # Each sample is given an experimenatlly-unique title.
            flat_sample = utils.flatten(sample_data)
            title = harmony.extract_title(flat_sample, title_field)

            # A sample may actually have many sub files.
            # If there is raw data, take that.
            # If not, take the derived.
            has_raw = False
            for sub_file in sample_data["file"]:

                # For ex: E-GEOD-15645
                if isinstance(sub_file["comment"], list):
                    sub_file_mod = sub_file
                    sub_file_mod["comment"] = sub_file["comment"][0]
                else:
                    sub_file_mod = sub_file

                # Some have the 'data' field, but not the actual data
                # Ex: E-GEOD-9656
                if (
                    sub_file_mod["type"] == "data"
                    and sub_file_mod["comment"].get("value", None) != None
                ):
                    has_raw = True

                # 'value' can be None, convert to an empty string to
                # make it easier to use.
                comment_value = sub_file_mod["comment"].get("value", "") or ""
                if "raw" in comment_value:
                    has_raw = True

            skip_sample = False
            for sub_file in sample_data["file"]:

                # Don't get the raw data if it's only a 1-color sample.
                if "Cy3" in str(sample_data) and "Cy5" not in str(sample_data):
                    has_raw = False

                # Skip derived data if we have it raw.
                if has_raw and "derived data" in sub_file["type"]:
                    continue

                download_url = None
                filename = sub_file["name"]

                # sub_file["comment"] is only a list if there's
                # more than one comment...
                comments = sub_file["comment"]
                if isinstance(comments, list):
                    # Could be: "Derived ArrayExpress Data Matrix FTP
                    # file" or: "ArrayExpress FTP file". If there is
                    # no comment with a name including "FTP file" then
                    # we don't know where to download it so we need to
                    # mark this job as an error. Therefore don't catch
                    # the potential exception where download_url
                    # doesn't get defined.
                    for comment in comments:
                        if "FTP file" in comment["name"]:
                            download_url = comment["value"]
                            break
                else:
                    download_url = comments["value"]

                if not download_url:
                    logger.error(
                        "Sample %s did not specify a download url, skipping.",
                        sample_accession_code,
                        experiment_accession_code=experiment.accession_code,
                        survey_job=self.survey_job.id,
                        sub_file=sub_file,
                    )
                    skip_sample = True
                    continue

                if not filename:
                    logger.error(
                        "Sample %s did not specify a filename, skipping.",
                        sample_accession_code,
                        experiment_accession_code=experiment.accession_code,
                        survey_job=self.survey_job.id,
                        sub_file=sub_file,
                    )
                    skip_sample = True
                    continue

            if skip_sample:
                continue

            # The accession code is not a simple matter to determine.
            sample_source_name = sample_data["source"].get("name", "")
            sample_assay_name = sample_data["assay"].get("name", "")
            sample_accession_code = self.determine_sample_accession(
                experiment.accession_code, sample_source_name, sample_assay_name, filename
            )

            # Figure out the Organism for this sample
            organism_name = UNKNOWN
            for characteristic in sample_data["characteristic"]:
                if characteristic["category"].upper() == "ORGANISM":
                    organism_name = characteristic["value"].upper()

            if organism_name == UNKNOWN:
                logger.error(
                    "Sample %s did not specify the organism name.",
                    sample_accession_code,
                    experiment_accession_code=experiment.accession_code,
                    survey_job=self.survey_job.id,
                )
                organism = None
                continue
            else:
                organism = Organism.get_object_for_name(organism_name)

            # Create the sample object
            try:
                # Associate it with the experiment, but since it
                # already exists it already has original files
                # associated with it and it's already been downloaded,
                # so don't add it to created_samples.
                sample_object = Sample.objects.get(accession_code=sample_accession_code)

                # If input experiment includes new protocol information,
                # update sample's protocol_info.
                existing_protocols = sample_object.protocol_info
                protocol_info, is_updated = self.update_sample_protocol_info(
                    existing_protocols,
                    experiment.protocol_description,
                    experiment.source_url + "/protocols",
                )
                if is_updated:
                    sample_object.protocol_info = protocol_info
                    sample_object.save()

                logger.debug(
                    "Sample %s already exists, skipping object creation.",
                    sample_accession_code,
                    experiment_accession_code=experiment.accession_code,
                    survey_job=self.survey_job.id,
                )
            except Sample.DoesNotExist:
                sample_object = Sample()

                # The basics
                sample_object.source_database = "ARRAY_EXPRESS"
                sample_object.title = title
                sample_object.accession_code = sample_accession_code
                sample_object.source_archive_url = samples_endpoint
                sample_object.organism = organism
                sample_object.platform_name = platform_dict["platform_accession_name"]
                sample_object.platform_accession_code = platform_dict["platform_accession_code"]
                sample_object.manufacturer = platform_dict["manufacturer"]
                sample_object.technology = "MICROARRAY"

                protocol_info, is_updated = self.update_sample_protocol_info(
                    existing_protocols=[],
                    experiment_protocol=experiment.protocol_description,
                    protocol_url=experiment.source_url + "/protocols",
                )
                # Do not check is_updated the first time because we must
                # save a list so we can append to it later.
                sample_object.protocol_info = protocol_info

                sample_object.save()

                # Directly assign the harmonized properties
                harmonized_sample = harmonized_samples[title]
                ArrayExpressSurveyor._apply_harmonized_metadata_to_sample(
                    sample_object, harmonized_sample
                )

                sample_annotation = SampleAnnotation()
                sample_annotation.data = sample_data
                sample_annotation.sample = sample_object
                sample_annotation.is_ccdl = False
                sample_annotation.save()

                original_file = OriginalFile()
                original_file.filename = filename
                original_file.source_filename = filename
                original_file.source_url = download_url
                original_file.is_downloaded = False
                original_file.is_archive = True
                original_file.has_raw = has_raw
                original_file.save()

                original_file_sample_association = OriginalFileSampleAssociation()
                original_file_sample_association.original_file = original_file
                original_file_sample_association.sample = sample_object
                original_file_sample_association.save()

                created_samples.append(sample_object)

                logger.debug(
                    "Created " + str(sample_object),
                    experiment_accession_code=experiment.accession_code,
                    survey_job=self.survey_job.id,
                    sample=sample_object.id,
                )

            # Create associations if they don't already exist
            ExperimentSampleAssociation.objects.get_or_create(
                experiment=experiment, sample=sample_object
            )

            ExperimentOrganismAssociation.objects.get_or_create(
                experiment=experiment, organism=organism
            )

        return created_samples

    def discover_experiment_and_samples(self) -> (Experiment, List[Sample]):

        experiment_accession_code = SurveyJobKeyValue.objects.get(
            survey_job_id=self.survey_job.id, key__exact="experiment_accession_code"
        ).value

        logger.info(
            "Surveying experiment with accession code: %s.",
            experiment_accession_code,
            survey_job=self.survey_job.id,
        )

        try:
            experiment, platform_dict = self.create_experiment_from_api(experiment_accession_code)
        except UnsupportedPlatformException:
            logger.info(
                "Experiment was not on a supported platform, skipping.",
                experiment_accession_code=experiment_accession_code,
                survey_job=self.survey_job.id,
            )
            return None, []
        except Exception:
            logger.exception(
                "Error occurred while surveying experiment!",
                experiment_accession_code=experiment_accession_code,
            )
            return None, []

        samples = self.create_samples_from_api(experiment, platform_dict)
        return experiment, samples
