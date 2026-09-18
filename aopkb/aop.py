from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict
from xsdata.models.datatype import XmlDateTime
from xsdata_pydantic.fields import field

__NAMESPACE__ = "http://www.aopkb.org/aop-xml"


class AopSource(Enum):
    AOPWIKI = "AOPWiki"
    EFFECTOPEDIA = "Effectopedia"
    INTERMEDIATE_EFFECTS_DB = "Intermediate Effects DB"
    AOP_XPLORER = "AOP Xplorer"
    OTHER = "Other"


class BiologicalOrganizationLevelType(Enum):
    MOLECULAR = "Molecular"
    ORGANELLE = "Organelle"
    CELLULAR = "Cellular"
    TISSUE = "Tissue"
    ORGAN = "Organ"
    ORGAN_SYSTEM = "Organ System"
    INDIVIDUAL = "Individual"
    POPULATION = "Population"
    SPECIES = "Species"
    COMMUNITIES = "Communities"
    ECOSYSTEM = "Ecosystem"
    BIOSPHERE = "Biosphere"


class BiologicalTermType(BaseModel):
    class Meta:
        name = "biological-term-type"

    model_config = ConfigDict(defer_build=True)
    source_id: None | str = field(
        default=None,
        metadata={
            "name": "source-id",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    source: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    name: str = field(
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )
    synonym: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )


class ConfidenceLevelType(Enum):
    HIGH = "High"
    MODERATE = "Moderate"
    LOW = "Low"
    NOT_SPECIFIED = "Not Specified"


class KeyEventRelationshipSource(Enum):
    AOPWIKI = "AOPWiki"
    EFFECTOPEDIA = "Effectopedia"
    INTERMEDIATE_EFFECTS_DB = "Intermediate Effects DB"
    AOP_XPLORER = "AOP Xplorer"
    OTHER = "Other"


class KeyEventSource(Enum):
    AOPWIKI = "AOPWiki"
    EFFECTOPEDIA = "Effectopedia"
    INTERMEDIATE_EFFECTS_DB = "Intermediate Effects DB"
    AOP_XPLORER = "AOP Xplorer"
    OTHER = "Other"


class LifeStageLifeStage(Enum):
    BIRTH_TO_1_MONTH = "Birth to < 1 month"
    VALUE_1_TO_3_MONTHS = "1 to < 3 months"
    VALUE_3_TO_6_MONTHS = "3 to < 6 months"
    VALUE_6_TO_12_MONTHS = "6 to < 12 months"
    VALUE_1_TO_2_YEARS = "1 to < 2 years"
    VALUE_2_TO_3_YEARS = "2 to < 3 years"
    VALUE_3_TO_6_YEARS = "3 to < 6 years"
    VALUE_6_TO_11_YEARS = "6 to < 11 years"
    VALUE_11_TO_16_YEARS = "11 to < 16 years"
    VALUE_16_TO_21_YEARS = "16 to < 21 years"
    NURSING_CHILD = "Nursing Child"
    PREGNANCY = "Pregnancy"
    OLD_AGE = "Old Age"
    NOT_OTHERWISE_SPECIFIED = "Not Otherwise Specified"
    LACTATING_MOTHER = "Lactating Mother"
    CONCEPTION_TO_FETAL = "Conception to < Fetal"
    FETAL_TO_PARTURITION = "Fetal to Parturition"
    FOETAL = "Foetal"
    EMBRYO = "Embryo"
    JUVENILE = "Juvenile"
    ADULT_REPRODUCTIVELY_MATURE = "Adult, reproductively mature"
    PERINATAL = "Perinatal"
    DURING_DEVELOPMENT_AND_AT_ADULTHOOD = "During development and at adulthood"
    ADULTS = "Adults"
    ADULT = "Adult"
    DURING_BRAIN_DEVELOPMENT_ADULTHOOD_AND_AGING = (
        "During brain development, adulthood and aging"
    )
    DURING_BRAIN_DEVELOPMENT = "During brain development"
    HUMAN = "Human"
    DEVELOPMENT = "Development"
    ALL_LIFE_STAGES = "All life stages"
    FETAL = "Fetal"
    LARVAE = "Larvae"
    LARVAL_DEVELOPMENT = "Larval development"
    BEFORE_OR_DURING_GONADAL_SEX_DIFFERENTIATION = (
        "before or during gonadal sex differentiation"
    )


class PropertyValues(BaseModel):
    class Meta:
        name = "property-values"

    model_config = ConfigDict(defer_build=True)
    property: str = field(
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )
    value: str = field(
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )
    units: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    conditions: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    source: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )


class QaType(BaseModel):
    class Meta:
        name = "qa-type"

    model_config = ConfigDict(defer_build=True)
    contributor: list[str] = field(
        default_factory=list,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    reviewer: list[str] = field(
        default_factory=list,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    seal_of_approval: list[str] = field(
        default_factory=list,
        metadata={
            "name": "seal-of-approval",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    last_modified: XmlDateTime = field(
        metadata={
            "name": "last-modified",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )


class RelationshipAdjacency(Enum):
    ADJACENT = "adjacent"
    NON_ADJACENT = "non-adjacent"


class SexSex(Enum):
    MALE = "Male"
    FEMALE = "Female"
    MIXED = "Mixed"
    ASEXUAL = "Asexual"
    THIRD_GENDER = "Third Gender"
    HERMAPHRODITE = "Hermaphrodite"
    UNSPECIFIC = "Unspecific"


class StatusOecdStatus(Enum):
    WPHA_WNT_ENDORSED = "WPHA/WNT Endorsed"
    ESCA_APPROVED = "ESCA Approved"
    UNDER_REVIEW = "Under Review"
    UNDER_DEVELOPMENT = "Under Development"


class StatusWikiLicense(Enum):
    ALL_RIGHTS_RESERVED = "All rights reserved"
    BY_SA = "BY-SA"
    OPEN_FOR_ADOPTION = "Open for adoption"


class TitleDetailLevel(Enum):
    HYPOTHETICAL = "Hypothetical"
    ONE_TO_ONE = "One to One"
    PROPORTIONAL = "Proportional"
    THRESHOLD = "Threshold"
    RESPONSE_RESPONSE = "Response-Response"


class UrlLink(BaseModel):
    class Meta:
        name = "url_link"

    model_config = ConfigDict(defer_build=True)
    link_source: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    url: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    title: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )


class EvidenceType(BaseModel):
    class Meta:
        name = "evidence-type"

    model_config = ConfigDict(defer_build=True)
    evidence: ConfidenceLevelType = field(
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )
    uri: list[str] = field(
        default_factory=list,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )


class Status(BaseModel):
    class Meta:
        name = "status"

    model_config = ConfigDict(defer_build=True)
    wiki_license: StatusWikiLicense = field(
        metadata={
            "name": "wiki-license",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )
    oecd_status: None | StatusOecdStatus = field(
        default=None,
        metadata={
            "name": "oecd-status",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )


class SubstanceInfo(BaseModel):
    class Meta:
        name = "substance-info"

    model_config = ConfigDict(defer_build=True)
    title: str = field(
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )
    id: str = field(
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )
    type_value: list[str] = field(
        default_factory=list,
        metadata={
            "name": "type",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    iuc_name: list[str] = field(
        default_factory=list,
        metadata={
            "name": "iuc-name",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    substance_properties: list[PropertyValues] = field(
        default_factory=list,
        metadata={
            "name": "substance-properties",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )


class ApplicabilityType(BaseModel):
    class Meta:
        name = "applicability-type"

    model_config = ConfigDict(defer_build=True)
    sex: list[ApplicabilityType.Sex] = field(
        default_factory=list,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    life_stage: list[ApplicabilityType.LifeStage] = field(
        default_factory=list,
        metadata={
            "name": "life-stage",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    taxonomy: list[ApplicabilityType.Taxonomy] = field(
        default_factory=list,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    uri: list[str] = field(
        default_factory=list,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    biological_compartment: list[str] = field(
        default_factory=list,
        metadata={
            "name": "biological-compartment",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    time_to_manifestation: list[str] = field(
        default_factory=list,
        metadata={
            "name": "time-to-manifestation",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    time_to_manifestation_range: list[str] = field(
        default_factory=list,
        metadata={
            "name": "time-to-manifestation-range",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    generation: list[str] = field(
        default_factory=list,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )

    class Sex(EvidenceType):
        model_config = ConfigDict(defer_build=True)
        sex: SexSex = field(
            metadata={
                "type": "Element",
                "namespace": "http://www.aopkb.org/aop-xml",
            }
        )

    class LifeStage(EvidenceType):
        model_config = ConfigDict(defer_build=True)
        life_stage: LifeStageLifeStage = field(
            metadata={
                "name": "life-stage",
                "type": "Element",
                "namespace": "http://www.aopkb.org/aop-xml",
            }
        )

    class Taxonomy(EvidenceType):
        model_config = ConfigDict(defer_build=True)
        taxonomy_id: None | str = field(
            default=None,
            metadata={
                "name": "taxonomy-id",
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            },
        )


class Data(BaseModel):
    class Meta:
        name = "data"
        namespace = "http://www.aopkb.org/aop-xml"

    model_config = ConfigDict(defer_build=True)
    chemical: list[Data.Chemical] = field(
        default_factory=list,
        metadata={
            "type": "Element",
        },
    )
    biological_object: list[Data.BiologicalObject] = field(
        default_factory=list,
        metadata={
            "name": "biological-object",
            "type": "Element",
        },
    )
    biological_process: list[Data.BiologicalProcess] = field(
        default_factory=list,
        metadata={
            "name": "biological-process",
            "type": "Element",
        },
    )
    biological_action: list[Data.BiologicalAction] = field(
        default_factory=list,
        metadata={
            "name": "biological-action",
            "type": "Element",
        },
    )
    stressor: list[Data.Stressor] = field(
        default_factory=list,
        metadata={
            "type": "Element",
        },
    )
    taxonomy: list[Data.Taxonomy] = field(
        default_factory=list,
        metadata={
            "type": "Element",
        },
    )
    key_event: list[Data.KeyEvent] = field(
        default_factory=list,
        metadata={
            "name": "key-event",
            "type": "Element",
        },
    )
    key_event_relationship: list[Data.KeyEventRelationship] = field(
        default_factory=list,
        metadata={
            "name": "key-event-relationship",
            "type": "Element",
        },
    )
    aop: list[Data.Aop] = field(
        default_factory=list,
        metadata={
            "type": "Element",
        },
    )
    vendor_specific: None | Data.VendorSpecific = field(
        default=None,
        metadata={
            "name": "vendor-specific",
            "type": "Element",
        },
    )

    class Chemical(BaseModel):
        model_config = ConfigDict(defer_build=True)
        inchi: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        casrn: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        jchem_inchi_key: None | str = field(
            default=None,
            metadata={
                "name": "jchem-inchi-key",
                "type": "Element",
            },
        )
        indigo_inchi_key: None | str = field(
            default=None,
            metadata={
                "name": "indigo-inchi-key",
                "type": "Element",
            },
        )
        iupac_name: None | str = field(
            default=None,
            metadata={
                "name": "iupac-name",
                "type": "Element",
            },
        )
        smiles: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        preferred_name: str = field(
            metadata={
                "name": "preferred-name",
                "type": "Element",
            }
        )
        synonyms: None | Data.Chemical.Synonyms = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        formula: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        structural_diagram_2d: None | bytes = field(
            default=None,
            metadata={
                "name": "structural-diagram-2d",
                "type": "Element",
                "format": "base16",
            },
        )
        dsstox_id: None | str = field(
            default=None,
            metadata={
                "name": "dsstox-id",
                "type": "Element",
            },
        )
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )

        class Synonyms(BaseModel):
            model_config = ConfigDict(defer_build=True)
            synonym: list[str] = field(
                default_factory=list,
                metadata={
                    "type": "Element",
                    "min_occurs": 1,
                },
            )

    class BiologicalObject(BiologicalTermType):
        model_config = ConfigDict(defer_build=True)
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )

    class BiologicalProcess(BiologicalTermType):
        model_config = ConfigDict(defer_build=True)
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )

    class BiologicalAction(BiologicalTermType):
        model_config = ConfigDict(defer_build=True)
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )

    class Stressor(BaseModel):
        model_config = ConfigDict(defer_build=True)
        name: str = field(
            metadata={
                "type": "Element",
            }
        )
        quality_assurance: None | QaType = field(
            default=None,
            metadata={
                "name": "quality-assurance",
                "type": "Element",
            },
        )
        description: str = field(
            metadata={
                "type": "Element",
            }
        )
        chemicals: None | Data.Stressor.Chemicals = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        structural_properties: list[PropertyValues] = field(
            default_factory=list,
            metadata={
                "name": "structural-properties",
                "type": "Element",
            },
        )
        synonym: list[str] = field(
            default_factory=list,
            metadata={
                "type": "Element",
            },
        )
        associated_substances: list[SubstanceInfo] = field(
            default_factory=list,
            metadata={
                "name": "associated-substances",
                "type": "Element",
            },
        )
        link: list[str] = field(
            default_factory=list,
            metadata={
                "type": "Element",
            },
        )
        exposure_characterization: None | str = field(
            default=None,
            metadata={
                "name": "exposure-characterization",
                "type": "Element",
            },
        )
        creation_timestamp: None | XmlDateTime = field(
            default=None,
            metadata={
                "name": "creation-timestamp",
                "type": "Element",
            },
        )
        last_modification_timestamp: None | XmlDateTime = field(
            default=None,
            metadata={
                "name": "last-modification-timestamp",
                "type": "Element",
            },
        )
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )

        class Chemicals(BaseModel):
            model_config = ConfigDict(defer_build=True)
            chemical_initiator: list[
                Data.Stressor.Chemicals.ChemicalInitiator
            ] = field(
                default_factory=list,
                metadata={
                    "name": "chemical-initiator",
                    "type": "Element",
                    "min_occurs": 1,
                },
            )

            class ChemicalInitiator(BaseModel):
                model_config = ConfigDict(defer_build=True)
                chemical_id: None | str = field(
                    default=None,
                    metadata={
                        "name": "chemical-id",
                        "type": "Attribute",
                        "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                    },
                )
                user_term: None | str = field(
                    default=None,
                    metadata={
                        "name": "user-term",
                        "type": "Attribute",
                    },
                )

    class Taxonomy(BiologicalTermType):
        model_config = ConfigDict(defer_build=True)
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )

    class KeyEvent(BaseModel):
        model_config = ConfigDict(defer_build=True)
        title: str = field(
            metadata={
                "type": "Element",
            }
        )
        short_name: str = field(
            metadata={
                "name": "short-name",
                "type": "Element",
            }
        )
        biological_organization_level: (
            None | BiologicalOrganizationLevelType
        ) = field(
            default=None,
            metadata={
                "name": "biological-organization-level",
                "type": "Element",
            },
        )
        description: str = field(
            metadata={
                "type": "Element",
            }
        )
        measurement_methodology: None | str = field(
            default=None,
            metadata={
                "name": "measurement-methodology",
                "type": "Element",
            },
        )
        evidence_supporting_taxonomic_applicability: None | str = field(
            default=None,
            metadata={
                "name": "evidence-supporting-taxonomic-applicability",
                "type": "Element",
            },
        )
        organ_term: None | BiologicalTermType = field(
            default=None,
            metadata={
                "name": "organ-term",
                "type": "Element",
            },
        )
        cell_term: None | BiologicalTermType = field(
            default=None,
            metadata={
                "name": "cell-term",
                "type": "Element",
            },
        )
        applicability: None | ApplicabilityType = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        associated_tests: None | str = field(
            default=None,
            metadata={
                "name": "associated-tests",
                "type": "Element",
            },
        )
        biological_events: None | Data.KeyEvent.BiologicalEvents = field(
            default=None,
            metadata={
                "name": "biological-events",
                "type": "Element",
            },
        )
        key_event_stressors: None | Data.KeyEvent.KeyEventStressors = field(
            default=None,
            metadata={
                "name": "key-event-stressors",
                "type": "Element",
            },
        )
        references: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        source: None | KeyEventSource = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        source_internal_id: None | str = field(
            default=None,
            metadata={
                "name": "source-internal-id",
                "type": "Element",
            },
        )
        uri: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        quality_assurance: None | QaType = field(
            default=None,
            metadata={
                "name": "quality-assurance",
                "type": "Element",
            },
        )
        creation_timestamp: None | XmlDateTime = field(
            default=None,
            metadata={
                "name": "creation-timestamp",
                "type": "Element",
            },
        )
        last_modification_timestamp: None | XmlDateTime = field(
            default=None,
            metadata={
                "name": "last-modification-timestamp",
                "type": "Element",
            },
        )
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )

        class BiologicalEvents(BaseModel):
            model_config = ConfigDict(defer_build=True)
            biological_event: list[
                Data.KeyEvent.BiologicalEvents.BiologicalEvent
            ] = field(
                default_factory=list,
                metadata={
                    "name": "biological-event",
                    "type": "Element",
                    "min_occurs": 1,
                },
            )

            class BiologicalEvent(BaseModel):
                model_config = ConfigDict(defer_build=True)
                object_id: None | str = field(
                    default=None,
                    metadata={
                        "name": "object-id",
                        "type": "Attribute",
                        "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                    },
                )
                process_id: None | str = field(
                    default=None,
                    metadata={
                        "name": "process-id",
                        "type": "Attribute",
                        "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                    },
                )
                action_id: str = field(
                    metadata={
                        "name": "action-id",
                        "type": "Attribute",
                        "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                    }
                )

        class KeyEventStressors(BaseModel):
            model_config = ConfigDict(defer_build=True)
            key_event_stressor: list[
                Data.KeyEvent.KeyEventStressors.KeyEventStressor
            ] = field(
                default_factory=list,
                metadata={
                    "name": "key-event-stressor",
                    "type": "Element",
                    "min_occurs": 1,
                },
            )

            class KeyEventStressor(EvidenceType):
                model_config = ConfigDict(defer_build=True)
                stressor_id: str = field(
                    metadata={
                        "name": "stressor-id",
                        "type": "Attribute",
                        "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                    }
                )

    class KeyEventRelationship(BaseModel):
        model_config = ConfigDict(defer_build=True)
        title: Data.KeyEventRelationship.Title = field(
            metadata={
                "type": "Element",
            }
        )
        description: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        evidence_collection_strategy: None | str = field(
            default=None,
            metadata={
                "name": "evidence-collection-strategy",
                "type": "Element",
            },
        )
        weight_of_evidence: (
            None | Data.KeyEventRelationship.WeightOfEvidence
        ) = field(
            default=None,
            metadata={
                "name": "weight-of-evidence",
                "type": "Element",
            },
        )
        known_modulating_factors: None | str = field(
            default=None,
            metadata={
                "name": "known-modulating-factors",
                "type": "Element",
            },
        )
        quantitative_understanding: (
            None | Data.KeyEventRelationship.QuantitativeUnderstanding
        ) = field(
            default=None,
            metadata={
                "name": "quantitative-understanding",
                "type": "Element",
            },
        )
        applicability: None | ApplicabilityType = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        evidence_supporting_taxonomic_applicability: None | str = field(
            default=None,
            metadata={
                "name": "evidence-supporting-taxonomic-applicability",
                "type": "Element",
            },
        )
        references: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        source: None | KeyEventRelationshipSource = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        source_internal_id: None | str = field(
            default=None,
            metadata={
                "name": "source-internal-id",
                "type": "Element",
            },
        )
        uri: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        quality_assurance: None | QaType = field(
            default=None,
            metadata={
                "name": "quality-assurance",
                "type": "Element",
            },
        )
        creation_timestamp: None | XmlDateTime = field(
            default=None,
            metadata={
                "name": "creation-timestamp",
                "type": "Element",
            },
        )
        last_modification_timestamp: None | XmlDateTime = field(
            default=None,
            metadata={
                "name": "last-modification-timestamp",
                "type": "Element",
            },
        )
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )

        class Title(BaseModel):
            model_config = ConfigDict(defer_build=True)
            upstream_id: str = field(
                metadata={
                    "name": "upstream-id",
                    "type": "Element",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                }
            )
            downstream_id: str = field(
                metadata={
                    "name": "downstream-id",
                    "type": "Element",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                }
            )
            detail_level: None | TitleDetailLevel = field(
                default=None,
                metadata={
                    "name": "detail-level",
                    "type": "Element",
                },
            )

        class WeightOfEvidence(BaseModel):
            model_config = ConfigDict(defer_build=True)
            value: str = field(
                metadata={
                    "type": "Element",
                }
            )
            biological_plausibility: None | str = field(
                default=None,
                metadata={
                    "name": "biological-plausibility",
                    "type": "Element",
                },
            )
            emperical_support_linkage: None | str = field(
                default=None,
                metadata={
                    "name": "emperical-support-linkage",
                    "type": "Element",
                },
            )
            uncertainties_or_inconsistencies: None | str = field(
                default=None,
                metadata={
                    "name": "uncertainties-or-inconsistencies",
                    "type": "Element",
                },
            )

        class QuantitativeUnderstanding(BaseModel):
            model_config = ConfigDict(defer_build=True)
            description: str = field(
                metadata={
                    "type": "Element",
                }
            )
            support_evidence: None | str = field(
                default=None,
                metadata={
                    "name": "support-evidence",
                    "type": "Element",
                },
            )
            response_response_relationship: None | str = field(
                default=None,
                metadata={
                    "name": "response-response-relationship",
                    "type": "Element",
                },
            )
            time_scale: None | str = field(
                default=None,
                metadata={
                    "name": "time-scale",
                    "type": "Element",
                },
            )
            feedforward_feedback_loops: None | str = field(
                default=None,
                metadata={
                    "name": "feedforward-feedback-loops",
                    "type": "Element",
                },
            )

    class Aop(BaseModel):
        model_config = ConfigDict(defer_build=True)
        title: str = field(
            metadata={
                "type": "Element",
            }
        )
        short_name: str = field(
            metadata={
                "name": "short-name",
                "type": "Element",
            }
        )
        point_of_contact: str = field(
            metadata={
                "name": "point-of-contact",
                "type": "Element",
            }
        )
        authors: str = field(
            metadata={
                "type": "Element",
            }
        )
        coaches: None | Data.Aop.Coaches = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        external_links: None | Data.Aop.ExternalLinks = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        status: Status = field(
            metadata={
                "type": "Element",
            }
        )
        oecd_project: str = field(
            metadata={
                "name": "oecd-project",
                "type": "Element",
            }
        )
        handbook_version: str = field(
            metadata={
                "name": "handbook-version",
                "type": "Element",
            }
        )
        abstract: str = field(
            metadata={
                "type": "Element",
            }
        )
        background: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        development_strategy: None | str = field(
            default=None,
            metadata={
                "name": "development-strategy",
                "type": "Element",
            },
        )
        molecular_initiating_event: list[Data.Aop.MolecularInitiatingEvent] = (
            field(
                default_factory=list,
                metadata={
                    "name": "molecular-initiating-event",
                    "type": "Element",
                },
            )
        )
        key_events: None | Data.Aop.KeyEvents = field(
            default=None,
            metadata={
                "name": "key-events",
                "type": "Element",
            },
        )
        adverse_outcome: list[Data.Aop.AdverseOutcome] = field(
            default_factory=list,
            metadata={
                "name": "adverse-outcome",
                "type": "Element",
            },
        )
        key_event_relationships: None | Data.Aop.KeyEventRelationships = field(
            default=None,
            metadata={
                "name": "key-event-relationships",
                "type": "Element",
            },
        )
        applicability: None | ApplicabilityType = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        overall_assessment: None | Data.Aop.OverallAssessment = field(
            default=None,
            metadata={
                "name": "overall-assessment",
                "type": "Element",
            },
        )
        potential_applications: None | str = field(
            default=None,
            metadata={
                "name": "potential-applications",
                "type": "Element",
            },
        )
        aop_stressors: None | Data.Aop.AopStressors = field(
            default=None,
            metadata={
                "name": "aop-stressors",
                "type": "Element",
            },
        )
        references: None | str = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        source: None | AopSource = field(
            default=None,
            metadata={
                "type": "Element",
            },
        )
        source_internal_id: None | str = field(
            default=None,
            metadata={
                "name": "source-internal-id",
                "type": "Element",
            },
        )
        uri: list[str] = field(
            default_factory=list,
            metadata={
                "type": "Element",
            },
        )
        quality_assurance: None | QaType = field(
            default=None,
            metadata={
                "name": "quality-assurance",
                "type": "Element",
            },
        )
        creation_timestamp: XmlDateTime = field(
            metadata={
                "name": "creation-timestamp",
                "type": "Element",
            }
        )
        last_modification_timestamp: XmlDateTime = field(
            metadata={
                "name": "last-modification-timestamp",
                "type": "Element",
            }
        )
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )

        class Coaches(BaseModel):
            model_config = ConfigDict(defer_build=True)
            coach: list[str] = field(
                default_factory=list,
                metadata={
                    "type": "Element",
                },
            )

        class ExternalLinks(BaseModel):
            model_config = ConfigDict(defer_build=True)
            url_link: list[UrlLink] = field(
                default_factory=list,
                metadata={
                    "type": "Element",
                },
            )

        class MolecularInitiatingEvent(BaseModel):
            model_config = ConfigDict(defer_build=True)
            evidence_supporting_chemical_initiation: None | str = field(
                default=None,
                metadata={
                    "name": "evidence-supporting-chemical-initiation",
                    "type": "Element",
                },
            )
            key_event_id: str = field(
                metadata={
                    "name": "key-event-id",
                    "type": "Attribute",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                }
            )

        class KeyEvents(BaseModel):
            model_config = ConfigDict(defer_build=True)
            key_event: list[Data.Aop.KeyEvents.KeyEvent] = field(
                default_factory=list,
                metadata={
                    "name": "key-event",
                    "type": "Element",
                },
            )

            class KeyEvent(BaseModel):
                model_config = ConfigDict(defer_build=True)
                key_event_id: str = field(
                    metadata={
                        "name": "key-event-id",
                        "type": "Attribute",
                        "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                    }
                )

        class AdverseOutcome(BaseModel):
            model_config = ConfigDict(defer_build=True)
            examples: None | str = field(
                default=None,
                metadata={
                    "type": "Element",
                },
            )
            regulatory_relevance: None | str = field(
                default=None,
                metadata={
                    "name": "regulatory-relevance",
                    "type": "Element",
                },
            )
            organs_affected: list[Data.Aop.AdverseOutcome.OrgansAffected] = (
                field(
                    default_factory=list,
                    metadata={
                        "name": "organs-affected",
                        "type": "Element",
                    },
                )
            )
            key_event_id: str = field(
                metadata={
                    "name": "key-event-id",
                    "type": "Attribute",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                }
            )

            class OrgansAffected(EvidenceType):
                model_config = ConfigDict(defer_build=True)
                synonym: str = field(
                    metadata={
                        "type": "Element",
                    }
                )
                scientific_name: str = field(
                    metadata={
                        "name": "scientific-name",
                        "type": "Element",
                    }
                )

        class KeyEventRelationships(BaseModel):
            model_config = ConfigDict(defer_build=True)
            relationship: list[Data.Aop.KeyEventRelationships.Relationship] = (
                field(
                    default_factory=list,
                    metadata={
                        "type": "Element",
                        "min_occurs": 1,
                    },
                )
            )

            class Relationship(BaseModel):
                model_config = ConfigDict(defer_build=True)
                adjacency: RelationshipAdjacency = field(
                    metadata={
                        "type": "Element",
                    }
                )
                quantitative_understanding_value: ConfidenceLevelType = field(
                    metadata={
                        "name": "quantitative-understanding-value",
                        "type": "Element",
                    }
                )
                evidence: ConfidenceLevelType = field(
                    metadata={
                        "type": "Element",
                    }
                )
                id: str = field(
                    metadata={
                        "type": "Attribute",
                        "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                    }
                )

        class OverallAssessment(BaseModel):
            model_config = ConfigDict(defer_build=True)
            description: None | str = field(
                default=None,
                metadata={
                    "type": "Element",
                },
            )
            applicability: None | str = field(
                default=None,
                metadata={
                    "type": "Element",
                },
            )
            key_event_essentiality_summary: None | str = field(
                default=None,
                metadata={
                    "name": "key-event-essentiality-summary",
                    "type": "Element",
                },
            )
            weight_of_evidence_summary: None | str = field(
                default=None,
                metadata={
                    "name": "weight-of-evidence-summary",
                    "type": "Element",
                },
            )
            known_modulating_factors: None | str = field(
                default=None,
                metadata={
                    "name": "known-modulating-factors",
                    "type": "Element",
                },
            )
            quantitative_considerations: None | str = field(
                default=None,
                metadata={
                    "name": "quantitative-considerations",
                    "type": "Element",
                },
            )
            uri: list[str] = field(
                default_factory=list,
                metadata={
                    "type": "Element",
                },
            )

        class AopStressors(BaseModel):
            model_config = ConfigDict(defer_build=True)
            aop_stressor: list[Data.Aop.AopStressors.AopStressor] = field(
                default_factory=list,
                metadata={
                    "name": "aop-stressor",
                    "type": "Element",
                    "min_occurs": 1,
                },
            )

            class AopStressor(EvidenceType):
                model_config = ConfigDict(defer_build=True)
                stressor_id: str = field(
                    metadata={
                        "name": "stressor-id",
                        "type": "Attribute",
                        "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                    }
                )

    class VendorSpecific(BaseModel):
        model_config = ConfigDict(defer_build=True)
        any_element: list[object] = field(
            default_factory=list,
            metadata={
                "type": "Wildcard",
                "namespace": "##any",
            },
        )
        id: str = field(
            metadata={
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )
        name: str = field(
            metadata={
                "type": "Attribute",
            }
        )
        version: None | str = field(
            default=None,
            metadata={
                "type": "Attribute",
            },
        )
