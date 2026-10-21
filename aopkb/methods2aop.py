from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict
from xsdata_pydantic.fields import field

__NAMESPACE__ = "http://www.aopkb.org/aop-xml"


class EndpointDirectionEnum(Enum):
    INCREASE = "increase"
    DECREASE = "decrease"
    CHANGE = "change"
    NOT_SPECIFIED = "not_specified"


class MethodTypeEnum(Enum):
    IN_VITRO = "in_vitro"
    IN_VIVO = "in_vivo"
    IN_SILICO = "in_silico"
    EX_VIVO = "ex_vivo"
    NOT_SPECIFIED = "not_specified"


class OntologyTermType(BaseModel):
    class Meta:
        name = "ontology-term-type"

    model_config = ConfigDict(defer_build=True)
    label: str = field(
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )
    iri: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    ontology: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )


class ThroughputEnum(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NOT_SPECIFIED = "not_specified"


class MeasurementMethodType(BaseModel):
    class Meta:
        name = "measurement-method-type"

    model_config = ConfigDict(defer_build=True)
    assay_name: str = field(
        metadata={
            "name": "assay-name",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        }
    )
    description: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    method_type: None | MethodTypeEnum = field(
        default=None,
        metadata={
            "name": "method-type",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    cell_types: None | MeasurementMethodType.CellTypes = field(
        default=None,
        metadata={
            "name": "cell-types",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    organ_tissues: None | MeasurementMethodType.OrganTissues = field(
        default=None,
        metadata={
            "name": "organ-tissues",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    species: None | MeasurementMethodType.Species = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    measured_endpoint: None | str = field(
        default=None,
        metadata={
            "name": "measured-endpoint",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    endpoint_direction: None | EndpointDirectionEnum = field(
        default=None,
        metadata={
            "name": "endpoint-direction",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    ontology_terms: None | MeasurementMethodType.OntologyTerms = field(
        default=None,
        metadata={
            "name": "ontology-terms",
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    guideline: None | str = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    throughput: None | ThroughputEnum = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )
    evidence: None | MeasurementMethodType.Evidence = field(
        default=None,
        metadata={
            "type": "Element",
            "namespace": "http://www.aopkb.org/aop-xml",
        },
    )

    class CellTypes(BaseModel):
        model_config = ConfigDict(defer_build=True)
        cell_type: list[str] = field(
            default_factory=list,
            metadata={
                "name": "cell-type",
                "type": "Element",
                "namespace": "http://www.aopkb.org/aop-xml",
                "min_occurs": 1,
            },
        )

    class OrganTissues(BaseModel):
        model_config = ConfigDict(defer_build=True)
        organ_tissue: list[str] = field(
            default_factory=list,
            metadata={
                "name": "organ-tissue",
                "type": "Element",
                "namespace": "http://www.aopkb.org/aop-xml",
                "min_occurs": 1,
            },
        )

    class Species(BaseModel):
        model_config = ConfigDict(defer_build=True)
        taxon: list[str] = field(
            default_factory=list,
            metadata={
                "type": "Element",
                "namespace": "http://www.aopkb.org/aop-xml",
                "min_occurs": 1,
            },
        )

    class OntologyTerms(BaseModel):
        model_config = ConfigDict(defer_build=True)
        ontology_term: list[OntologyTermType] = field(
            default_factory=list,
            metadata={
                "name": "ontology-term",
                "type": "Element",
                "namespace": "http://www.aopkb.org/aop-xml",
                "min_occurs": 1,
            },
        )

    class Evidence(BaseModel):
        model_config = ConfigDict(defer_build=True)
        source_text: list[str] = field(
            default_factory=list,
            metadata={
                "name": "source-text",
                "type": "Element",
                "namespace": "http://www.aopkb.org/aop-xml",
                "min_occurs": 1,
            },
        )


class Assays(BaseModel):
    """
    Collection of structured assay records.

    Each assay is linked to the Key Event it was extracted for through
    ke-id (the Key Event GUID). The optional lookup section maps the GUIDs
    to stable, human-readable ids.
    """

    class Meta:
        name = "assays"
        namespace = "http://www.aopkb.org/aop-xml"

    model_config = ConfigDict(defer_build=True)
    techniques: None | Assays.Techniques = field(
        default=None,
        metadata={
            "type": "Element",
        },
    )
    assay: list[Assays.Assay] = field(
        default_factory=list,
        metadata={
            "type": "Element",
        },
    )
    lookup: None | Assays.Lookup = field(
        default=None,
        metadata={
            "type": "Element",
        },
    )

    class Techniques(BaseModel):
        model_config = ConfigDict(defer_build=True)
        technique: list[Assays.Techniques.Technique] = field(
            default_factory=list,
            metadata={
                "type": "Element",
            },
        )

        class Technique(BaseModel):
            model_config = ConfigDict(defer_build=True)
            name: str = field(
                metadata={
                    "type": "Element",
                }
            )
            synonym: list[str] = field(
                default_factory=list,
                metadata={
                    "type": "Element",
                },
            )
            id: str = field(
                metadata={
                    "type": "Attribute",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                }
            )

    class Assay(BaseModel):
        model_config = ConfigDict(defer_build=True)
        method: MeasurementMethodType = field(
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
        ke_id: str = field(
            metadata={
                "name": "ke-id",
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )
        technique_id: None | str = field(
            default=None,
            metadata={
                "name": "technique-id",
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            },
        )
        support: None | int = field(
            default=None,
            metadata={
                "type": "Attribute",
            },
        )
        models: list[str] = field(
            default_factory=list,
            metadata={
                "type": "Attribute",
                "tokens": True,
            },
        )

    class Lookup(BaseModel):
        model_config = ConfigDict(defer_build=True)
        key_event_reference: list[Assays.Lookup.KeyEventReference] = field(
            default_factory=list,
            metadata={
                "name": "key-event-reference",
                "type": "Element",
            },
        )
        technique_reference: list[Assays.Lookup.TechniqueReference] = field(
            default_factory=list,
            metadata={
                "name": "technique-reference",
                "type": "Element",
            },
        )
        assay_reference: list[Assays.Lookup.AssayReference] = field(
            default_factory=list,
            metadata={
                "name": "assay-reference",
                "type": "Element",
            },
        )

        class KeyEventReference(BaseModel):
            model_config = ConfigDict(defer_build=True)
            id: str = field(
                metadata={
                    "type": "Attribute",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                }
            )
            aop_wiki_id: int = field(
                metadata={
                    "name": "aop-wiki-id",
                    "type": "Attribute",
                }
            )

        class TechniqueReference(BaseModel):
            model_config = ConfigDict(defer_build=True)
            id: str = field(
                metadata={
                    "type": "Attribute",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                }
            )
            technique_id: str = field(
                metadata={
                    "name": "technique-id",
                    "type": "Attribute",
                }
            )

        class AssayReference(BaseModel):
            model_config = ConfigDict(defer_build=True)
            id: str = field(
                metadata={
                    "type": "Attribute",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                }
            )
            assay_id: str = field(
                metadata={
                    "name": "assay-id",
                    "type": "Attribute",
                }
            )


class KeyEventExtensionExample(BaseModel):
    """
    This element is for documentation only — it shows the proposed
    extension to the key-event sequence.

    In the actual schema, the content below should be inserted inside the
    existing key-event complexType/sequence, after the existing
    measurement-methodology element.

    :ivar measurement_methodology: Legacy free-text field. Retained for
        backward compatibility. New content should be added to
        measurement-methods instead.
    :ivar measurement_methods: Structured replacement/extension for
        measurement-methodology. Contains one method element per
        distinct assay described. Aligned with Methods2AOP framework
        (Karmaus et al. 2025).
    """

    class Meta:
        name = "key-event-extension-example"
        namespace = "http://www.aopkb.org/aop-xml"

    model_config = ConfigDict(defer_build=True)
    measurement_methodology: None | str = field(
        default=None,
        metadata={
            "name": "measurement-methodology",
            "type": "Element",
        },
    )
    measurement_methods: None | KeyEventExtensionExample.MeasurementMethods = (
        field(
            default=None,
            metadata={
                "name": "measurement-methods",
                "type": "Element",
            },
        )
    )

    class MeasurementMethods(BaseModel):
        """
        :ivar method: One structured record per assay. Multiple method
            elements within one measurement-methods block represent
            distinct assays measured for this Key Event.
        """

        model_config = ConfigDict(defer_build=True)
        method: list[MeasurementMethodType] = field(
            default_factory=list,
            metadata={
                "type": "Element",
                "min_occurs": 1,
            },
        )
