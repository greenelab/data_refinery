"""
Given a list of samples and their metadata, extract these common properties:

  `title`,
  `sex`,
  `age`,
  `specimen_part`,
  `genetic_information`,
  `disease`,
  `disease_stage`,
  `cell_line`,
  `treatment`,
  `race`,
  `subject`,
  `compound`,
  `time`

Array Express Example:
     {'Array Data File': 'C30061.CEL',
      'Array Design REF': 'A-AFFY-1',
      'Assay Name': '1009003-C30061',
      'Characteristics[age]': '38',
      'Characteristics[developmental stage]': 'adult',
      'Characteristics[organism part]': 'islet',
      'Characteristics[organism]': 'Homo sapiens',
      'Characteristics[sex]': 'male',
      'Comment [ArrayExpress FTP file]': 'ftp://ftp.ebi.ac.uk/pub/databases/microarray/data/experiment/MTAB/E-MTAB-3050/E-MTAB-3050.raw.1.zip',
      'Comment [Derived ArrayExpress FTP file]': 'ftp://ftp.ebi.ac.uk/pub/databases/microarray/data/experiment/MTAB/E-MTAB-3050/E-MTAB-3050.processed.1.zip',
      'Derived Array Data File': 'C30061.txt',
      'Description': 'Islets from 38 years old male. Time from islet preparation '
                     'to culture initiation: 96 hours. Provided by the North West '
                     'Tissue Center Seattle.',
      'Extract Name': 'donor B differentiated cells RNA',
      'Factor Value[cell type]': 'differentiated',
      'Factor Value[individual]': 'B',
      'Factor Value[test result]': 'unsuccessful',
      'Image File': 'C30061.DAT',
      'Label': 'biotin',
      'Labeled Extract Name': 'donor B differentiated cells LEX',
      'Material Type': 'cell',
      'Protocol REF': 'P-MTAB-41862',
      '    ': 'donor B islets',
      'Technology Type': 'array assay',
      'Unit [time unit]': 'year',
      'sex': 'male'}

SRA Example:
    {'alias': 'GSM2997959_r1',
     'broker_name': 'GEO',
     'center_name': 'GEO',
     'center_project_name': 'GSE99065',
     'ena-base-count': '279111754922',
     'ena-spot-count': '1152605026',
     'experiment_accession': 'SRX3691797',
     'experiment_design_description': None,
     'experiment_title': 'NextSeq 500 paired end sequencing; GSM2997959: INOF_FRT; '
                         'Homo sapiens; RNA-Seq',
     'lab_name': '',
     'library_construction_protocol': 'cDNA library produced with TGIRT RNA was '
                                      'isolated and DNase treated using RNEasy '
                                      'mini kit (Qiagen 74106) according to the '
                                      'manufacturer protocol. 5ug DNA-free total '
                                      'RNA was then ribodepleted using Ribo-zero '
                                      'Gold (Illumina RZG1224 ) according to the '
                                      'manufacturer protocol and purified using a '
                                      'modified ZYMO RNA Clean and Concentrator '
                                      '(R1016) protocol where 8 volumes EtOH '
                                      'instead of 4. rRNA depleted RNA was '
                                      'fragmented with NEBNext Magnesium RNA '
                                      'Fragmentation Module (E6150) followed by '
                                      'dephosphorylation using T4PNK (mandel  ) '
                                      'and purified by same modified ZYMO '
                                      'protocol. cDNAs were synthesized via TGIRT '
                                      'template-switching with 1µM TGIRT-III '
                                      'reverse transcriptase (Ingex, LLC) for 15 '
                                      'min at 60o C, during which a DNA '
                                      'oligonucleotide containing the complement '
                                      'of an Illumina Read 2 sequencing '
                                      'primer-binding site becomes seamlessly '
                                      "linked to the 5' cDNA end. After reaction "
                                      'cleanup (Qiagen MinElute Reaction cleanup '
                                      "28206), a 5' adenylated DNA oligonucleotide "
                                      'containing the complement of an Illumina '
                                      'Read 1 sequencing primer-binding site is '
                                      "then ligated to the 3' cDNA end with "
                                      "Thermostable 5' AppDNA / RNA Ligase (New "
                                      'England Biolabs M0319). Properly ligated '
                                      'cDNAs were amplified by PCR (12 cycles) to '
                                      'synthesize the second strand and add '
                                      'Illumina flowcell capture and index '
                                      'sequences. Library was size-selected with '
                                      'Ampure XP beads (Beckman-Coulter) and '
                                      'quantified with Qubit and evaluated on an '
                                      'Agilent 2100 Bioanalyzer.',
     'library_layout': 'PAIRED',
     'library_selection': 'cDNA',
     'library_source': 'TRANSCRIPTOMIC',
     'library_strategy': 'RNA-Seq',
     'organism_id': '9606',
     'organism_name': 'HOMO SAPIENS',
     'platform_instrument_model': 'NextSeq500',
     'run_accession': 'SRR6718414',
     'run_ena_base_count': '1773379870',
     'run_ena_first_public': '2018-02-17',
     'run_ena_last_update': '2018-02-17',
     'run_ena_spot_count': '15046271',
     'sample_accession': 'SRS2951393',
     'sample_cell_type': 'Immortalized normal ovarian fibroblast',
     'sample_ena_base_count': '1773379870',
     'sample_ena_first_public': '2018-02-14',
     'sample_ena_last_update': '2018-02-14',
     'sample_ena_spot_count': '15046271',
     'sample_source_name': 'INOF cell line',
     'sample_title': 'INOF_FRT',
     'sample_treatment': 'none',
     'study_abstract': 'The ability to compare the abundance of one RNA molecule '
                       'to another is a crucial step for understanding how gene '
                       'expression is modulated to shape the transcriptome '
                       'landscape. However, little information is available about '
                       'the relative expression of the different classes of coding '
                       'and non-coding RNA or even between RNA of the same class. '
                       'In this study, we present a complete portrait of the human '
                       'transcriptome that depicts the relationship of all classes '
                       'of non-ribosomal RNA longer than sixty nucleotides. The '
                       'results show that the most abundant RNA in the human '
                       'rRNA-depleted transcriptome is tRNA followed by '
                       'spliceosomal RNA. Surprisingly, the signal recognition '
                       'particle RNA 7SL by itself occupied 8% of the ribodepleted '
                       'transcriptome producing a similar number of transcripts as '
                       'that produced by all snoRNA genes combined. In general, '
                       'the most abundant RNA are non-coding but many more protein '
                       'coding than non-coding genes produce more than 1 '
                       'transcript per million. Examination of gene functions '
                       'suggests that RNA abundance reflects both gene and cell '
                       'function. Together, the data indicate that the human '
                       'transcriptome is shaped by a small number of highly '
                       'expressed non-coding genes and a large number of '
                       'moderately expressed protein coding genes that reflect '
                       'cellular phenotypes. Overall design: RNA was isolated from '
                       'SKOV3ip1 and INOF human cell lines and selected with '
                       'different methods. The resulting libraries were '
                       'multiplexed and paired-end sequenced using Illumina HiSeq.',
     'study_accession': 'SRP107324',
     'study_ena_base_count': '279111754922',
     'study_ena_first_public': '2017-09-25',
     'study_ena_last_update': '2018-02-15',
     'study_ena_spot_count': '1152605026',
     'study_title': 'Simultaneous detection and relative quantification of coding '
                    'and non-coding RNA using a single sequencing reaction',
     'study_type': 'Transcriptome Analysis',
     'submission_accession': 'SRA562540',
     'submission_comment': 'submission brokered by GEO',
     'submission_title': 'Submitted by Gene Expression Omnibus on 25-SEP-2017'}

GEO:
    ex:
        {'channel_count': ['1'],
         'characteristics_ch1': ['patient: P-39',
                                 'gender: female',
                                 'age: 65',
                                 'location: lower leg',
                                 'transplanted organ: kidney',
                                 'immunosuppressive drugs: azathioprine + prednison',
                                 'sample type: squamous cell carcinoma',
                                 'cell type: keratinocyte'],
         'contact_address': ['Einthovenweg 20'],
         'contact_city': ['Leiden'],
         'contact_country': ['Netherlands'],
         'contact_department': ['Toxicogenetics, S4-P'],
         'contact_email': ['h.vrieling@lumc.nl'],
         'contact_institute': ['Leiden University Medical Center'],
         'contact_laboratory': ['room T4-34'],
         'contact_name': ['Harry,,Vrieling'],
         'contact_zip/postal_code': ['2333 ZC'],
         'data_processing': ['Raw data was extracted from the BeadChip data files in '
                             'Illumina’s BeadStudio Version 3.2 software using the '
                             'gene expression module (v 3.2.7). Background subtracted '
                             'data was further analyzed in R-based Bioconductor '
                             'package, lumi (version 1.12.4). In lumi, the data was '
                             'transformed (variance-stabilizing transformation (VST)) '
                             'and normalized (robust spline normalization (RSN)), '
                             'resulting in log-transformed normalized data. The '
                             'R-package illuminaHumanv2.db (version 1.4.1) was used '
                             'for annotation. The data were purged of genes that did '
                             'not meet the detection limit (expression-detection '
                             'P-value >0.01) and/or were not annotated. The limma '
                             'R-package (version 3.2.3) was used to identify '
                             'differentially expressed genes (DEGs) between SCC, AK '
                             'and NS. Gene set enrichment analysis (GSEA) was '
                             'performed with the significantly DEGs from the limma '
                             'analysis using DAVID Bioinformatic Resources v6.7 '
                             '(http://david.abcc.ncifcrf.gov). GSEA on the entire data '
                             'set was performed using the parametric gene set '
                             'enrichment analysis (PGSEA) R-package (version '
                             '1.14.0).      To identify activation of transcription '
                             'factors in AKs and SCCs, the DEGs from the limma '
                             'analysis were investigated using the online analysis '
                             'tool oPOSSUM.',
                             'Matrix normalized matrix shows VST-transformed, '
                             'RSN-normalized data (used scripts from lumi package)',
                             'Matrix non-normalized: AVG_Signal: average signal for '
                             'the probe; BEAD_STDERR: standard error of the beads; '
                             'Avg_NBEADS: average number of beads for that probe; '
                             'Detection Pval: detection p-value. All extracted from '
                             'Beadstudio'],
         'data_row_count': ['48701'],
         'description': ['1881436235_A'],
         'extract_protocol_ch1': ['RNA was isolated from SCC and AK samples that '
                                  'contained at least 70% tumor cells, as determined '
                                  'by haematoxylin and eosin stained frozen sections. '
                                  'From the sample of unexposed NS the epidermis was '
                                  'removed for further processing by cryosectioning '
                                  'parallel to the outer surface of the skin biopsy. '
                                  'RNA was extracted from frozen material using the '
                                  'RNeasy Fibrous Tissue kit (Qiagen), which included '
                                  'proteinase K treatment (10 min at 55˚C) of the '
                                  'lysed sample in RLT-buffer and on-column DNase '
                                  'treatment. RNA was quantified using a Nanodrop '
                                  '(NanoDrop technologies) and evaluated for '
                                  'degradation with a RNA 6000 Nano Labchip on the '
                                  '2100 Bioanalyzer (Agilent Technologies)'],
         'geo_accession': ['GSM808778'],
         'hyb_protocol': ['The standard Illumina hybridization protocol was used. In '
                          'brief, the samples were hybridized to the arrays at 58ºC '
                          'overnight.'],
         'label_ch1': ['biotin'],
         'label_protocol_ch1': ['100 ng of total RNA was converted to cDNA and '
                                'subsequently labeled cRNA using the Ambion Illumina '
                                'TotalPrep RNA Amplification kit (Ambion) according to '
                                'manufacturer’s instructions'],
         'last_update_date': ['Feb 06 2013'],
         'molecule_ch1': ['total RNA'],
         'organism_ch1': ['Homo sapiens'],
         'platform_id': ['GPL6102'],
         'scan_protocol': ['The beadChips were scanned using the Illumina BeadArray '
                           'Reader, using the standard Illumina scanning protocol'],
         'series_id': ['GSE32628', 'GSE32969', 'GSE32979'],
         'source_name_ch1': ['cutaneous squamous cell carcinoma'],
         'status': ['Public on Feb 06 2013'],
         'submission_date': ['Oct 05 2011'],
         'supplementary_file': ['NONE'],
         'taxid_ch1': ['9606'],
         'title': ['SCC_P-39'],
         'type': ['RNA']}
"""

import csv
from io import StringIO
from typing import Dict, List

from data_refinery_common.logging import get_and_configure_logger
from data_refinery_foreman.surveyor.utils import requests_retry_session

logger = get_and_configure_logger(__name__)


TITLE_FIELDS = [
    "sample name",
    "sample title",
    "title",
    "subject number",
    "extract name",
    "labeled extract name",
]


def extract_title(sample: Dict, priority_field: str = None) -> str:
    """ Given a flat sample dictionary, find the title """
    if priority_field:
        title_fields = [priority_field] + [tf for tf in TITLE_FIELDS if tf != priority_field]
    else:
        title_fields = TITLE_FIELDS

    # Specifically look up for imported, non-SDRF AE samples
    for comment in sample.get("source_comment", []):
        if "title" in comment.get("name", ""):
            return comment["value"]

    expanded_title_fields = create_variants(title_fields)

    for title_field in expanded_title_fields:
        if title_field in sample:
            return sample[title_field]

    # If we can't even find a unique title for this sample
    # something has gone horribly wrong.
    return None


def create_variants(fields_list: List):
    """ Given a list of strings, create variations likely to give metadata hits.

    Ex, given 'cell line', add the ability to hit on 'characteristic [cell_line]' as well.
    """
    variants = []

    for field in fields_list:
        space_variants = [field]
        # Variate forms of multi-word strings
        if " " in field:
            space_variants.append(field.replace(" ", "_"))
            space_variants.append(field.replace(" ", "-"))
            space_variants.append(field.replace(" ", ""))

        for space_variant in space_variants:
            variants.append(space_variant)
            variants.append("characteristic [" + space_variant + "]")
            variants.append("characteristic[" + space_variant + "]")
            variants.append("characteristics [" + space_variant + "]")
            variants.append("characteristics[" + space_variant + "]")
            variants.append("comment [" + space_variant + "]")
            variants.append("comment[" + space_variant + "]")
            variants.append("comments [" + space_variant + "]")
            variants.append("comments[" + space_variant + "]")
            variants.append("factorvalue[" + space_variant + "]")
            variants.append("factor value[" + space_variant + "]")
            variants.append("factorvalue [" + space_variant + "]")
            variants.append("factor value [" + space_variant + "]")
            variants.append("sample_" + space_variant)
            variants.append("sample_host" + space_variant)
            variants.append("sample_sample_" + space_variant)  # Yes, seriously.

    return variants


def parse_sdrf(sdrf_url: str) -> List:
    """ Given a URL to an SDRF file, download parses it into JSON. """

    try:
        sdrf_response = requests_retry_session().get(sdrf_url, timeout=60)
    except Exception:
        logger.exception("Unable to fetch URL: " + sdrf_url)
        return []

    if sdrf_response.status_code != 200:
        logger.error("Unable to fetch URL: " + sdrf_url, response_code=sdrf_response.status_code)
        return []

    sdrf_text = sdrf_response.text

    samples = []

    reader = csv.reader(StringIO(sdrf_text), delimiter="\t")
    for offset, line in enumerate(reader):

        # Get the keys
        if offset == 0:
            keys = line
            continue

        sample_values = line

        # Skip malformed lines
        if len(sample_values) != len(keys):
            continue

        sample = {}
        for col, value in enumerate(sample_values):
            key = keys[col]
            sample[key.strip().lower()] = value
        samples.append(sample)

    return samples


def preprocess_geo_sample(sample) -> List:
    """
    Prepares items from GEO for harmonization
    """
    new_sample = {}
    for key, value in sample.metadata.items():

        if key == "characteristics_ch1":
            for pair in value:

                # This will almost always happen, except if we get
                # a malformed response from the server.
                if ":" in pair:
                    split = pair.split(":", 1)
                    new_sample[split[0].strip().lower()] = split[1].strip()
            continue

        # Probably won't be a list with length greater than one,
        # but maybe?
        new_sample[key.strip().lower()] = " ".join(value)

    return new_sample


class Harmonizer:
    def __init__(self):
        sex_fields = [
            "sex",
            "gender",
            "subject gender",
            "subjext sex",
            # This looks reduntant, but there are some samples which use
            # Characteristic[Characteristic[sex]]
            "characteristic [sex]",
            "characteristics [sex]",
        ]
        self.sex_fields = create_variants(sex_fields)

        age_fields = [
            "age",
            "patient age",
            "age of patient",
            "age (years)",
            "age (yrs)",
            "age (months)",
            "age (days)",
            "age (hours)",
            "age at diagnosis",
            "age at diagnosis years",
            "age at diagnosis months",
            "age at diagnosis days",
            "age at diagnosis hours",
            "characteristic [age]",
            "characteristics [age]",
        ]
        self.age_fields = create_variants(age_fields)

        specimen_part_fields = [
            # AE
            "organism part",
            "cell type",
            "tissue",
            "tissue type",
            "tissue source",
            "tissue origin",
            "source tissue",
            "tissue subtype",
            "tissue/cell type",
            "tissue region",
            "tissue compartment",
            "tissues",
            "tissue of origin",
            "tissue-type",
            "tissue harvested",
            "cell/tissue type",
            "tissue subregion",
            "organ",
            "characteristic [organism part]",
            "characteristics [organism part]",
            # SRA
            "cell_type",
            "organismpart",
            # GEO
            "isolation source",
            "tissue sampled",
            "cell description",
        ]
        self.specimen_part_fields = create_variants(specimen_part_fields)

        genetic_information_fields = [
            "strain/background",
            "strain",
            "strain or line",
            "background strain",
            "genotype",
            "genetic background",
            "genetic information",
            "genotype/variation",
            "ecotype",
            "cultivar",
            "strain/genotype",
        ]
        self.genetic_information_fields = create_variants(genetic_information_fields)

        disease_fields = [
            "disease",
            "disease state",
            "disease status",
            "diagnosis",
            "disease",
            "infection with",
            "sample type",
        ]
        self.disease_fields = create_variants(disease_fields)

        disease_stage_fields = [
            "disease state",
            "disease staging",
            "disease stage",
            "grade",
            "tumor grade",
            "who grade",
            "histological grade",
            "tumor grading",
            "disease outcome",
            "subject status",
        ]
        self.disease_stage_fields = create_variants(disease_stage_fields)

        cell_line_fields = [
            "cell line",
            "sample strain",
        ]
        self.cell_line_fields = create_variants(cell_line_fields)

        treatment_fields = [
            "treatment",
            "treatment group",
            "treatment protocol",
            "drug treatment",
            "clinical treatment",
        ]
        self.treatment_fields = create_variants(treatment_fields)

        race_fields = [
            "race",
            "ethnicity",
            "race/ethnicity",
        ]
        self.race_fields = create_variants(race_fields)

        subject_fields = [
            # AE
            "subject",
            "subject id",
            "subject/sample source id",
            "subject identifier",
            "human subject anonymized id",
            "individual",
            "individual identifier",
            "individual id",
            "patient",
            "patient id",
            "patient identifier",
            "patient number",
            "patient no",
            "donor id",
            "donor",
            # SRA
            "sample_source_name",
        ]
        self.subject_fields = create_variants(subject_fields)

        developmental_stage_fields = [
            "developmental stage",
            "development stage",
            "development stages",
        ]
        self.developmental_stage_fields = create_variants(developmental_stage_fields)

        compound_fields = [
            "compound",
            "compound1",
            "compound2",
            "compound name",
            "drug",
            "drugs",
            "immunosuppressive drugs",
        ]
        self.compound_fields = create_variants(compound_fields)

        time_fields = [
            "time",
            "initial time point",
            "start time",
            "stop time",
            "time point",
            "sampling time point",
            "sampling time",
            "time post infection",
        ]
        self.time_fields = create_variants(time_fields)

    def harmonize_value(self, field_name: str, value):
        if field_name == "age":
            try:
                return float(value)
            except ValueError:
                try:
                    return float(value.split(" ")[0])
                except ValueError:
                    # This is probably something weird, like a '.'
                    return
        elif field_name == "sex":
            if value.lower() in ["f", "female", "woman"]:
                return "female"
            elif value.lower() in ["m", "male", "man"]:
                return "male"
            else:
                return value.lower()
        else:
            return value.lower().strip()

    def harmonize_field(self, sample_metadata: Dict, harmonized_sample: Dict, field: str):
        field_name = field.split("_fields")[0]

        for key, value in sample_metadata.items():
            lower_key = key.lower().strip()

            if lower_key in getattr(self, field):
                harmonized_value = self.harmonize_value(field_name, value)

                if harmonized_value:
                    harmonized_sample[field_name] = harmonized_value
                    break

    def harmonize_sample(self, sample_metadata: Dict, title_field: str = None) -> Dict:
        fields = [
            "sex_fields",
            "age_fields",
            "specimen_part_fields",
            "genetic_information_fields",
            "disease_fields",
            "disease_stage_fields",
            "cell_line_fields",
            "treatment_fields",
            "race_fields",
            "subject_fields",
            "developmental_stage_fields",
            "compound_fields",
            "time_fields",
        ]

        harmonized_sample = {}
        harmonized_sample["title"] = extract_title(sample_metadata, title_field)
        for field in fields:
            self.harmonize_field(sample_metadata, harmonized_sample, field)

        return harmonized_sample


def determine_title_field(a_samples: List[Dict], b_samples: List[Dict]) -> str:
    """Determines which field should be used for the title of the sample.

    Sometimes there is more metadata than actual samples, so we just
    take the field with the largest number of matching values.
    """
    max_title_field = ""
    max_matching_titles = 0
    for title_field in TITLE_FIELDS:
        a_titles = {extract_title(sample, title_field) for sample in a_samples}
        b_titles = {extract_title(sample, title_field) for sample in b_samples}

        if a_titles == b_titles:
            max_title_field = title_field
            max_matching_titles = len(a_titles)

    if max_matching_titles > 0:
        return max_title_field


def harmonize_all_samples(sample_metadata: List[Dict], title_field: str = None) -> Dict:
    """Returns a mapping of sample title to harmonized sample metadata.

    See docstring at top of file for further clarfication of what "harmonized" means."""
    harmonizer = Harmonizer()

    harmonized_samples = {}
    for sample in sample_metadata:
        harmonized_sample = harmonizer.harmonize_sample(sample, title_field)
        title = harmonized_sample["title"]
        harmonized_samples[title] = harmonized_sample

    return harmonized_samples
