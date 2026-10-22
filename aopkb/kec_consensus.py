from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict
from xsdata_pydantic.fields import field

__NAMESPACE__ = "http://www.aopkb.org/kec-consensus"


class ClusterStatusEnum(Enum):
    CONFIRMED = "confirmed"
    NOVEL = "novel"


class CuratedStatusEnum(Enum):
    CONFIRMED = "confirmed"
    MISSED = "missed"


class TermKindEnum(Enum):
    OBJECT = "object"
    PROCESS = "process"
    ACTION = "action"


class KecConsensus(BaseModel):
    """
    Cross-model consensus triples of Key Events.

    See the file header.
    """

    class Meta:
        name = "kec-consensus"
        namespace = "http://www.aopkb.org/kec-consensus"

    model_config = ConfigDict(defer_build=True)
    summary: KecConsensus.Summary = field(
        metadata={
            "type": "Element",
        }
    )
    terms: None | KecConsensus.Terms = field(
        default=None,
        metadata={
            "type": "Element",
        },
    )
    key_event_result: list[KecConsensus.KeyEventResult] = field(
        default_factory=list,
        metadata={
            "name": "key-event-result",
            "type": "Element",
        },
    )
    consensus_triple: list[KecConsensus.ConsensusTriple] = field(
        default_factory=list,
        metadata={
            "name": "consensus-triple",
            "type": "Element",
        },
    )
    lookup: None | KecConsensus.Lookup = field(
        default=None,
        metadata={
            "type": "Element",
        },
    )

    class Summary(BaseModel):
        model_config = ConfigDict(defer_build=True)
        model: list[str] = field(
            default_factory=list,
            metadata={
                "type": "Element",
            },
        )
        support_histogram: None | KecConsensus.Summary.SupportHistogram = (
            field(
                default=None,
                metadata={
                    "name": "support-histogram",
                    "type": "Element",
                },
            )
        )
        n_models: int = field(
            metadata={
                "name": "n-models",
                "type": "Attribute",
            }
        )
        restricted: bool = field(
            metadata={
                "type": "Attribute",
            }
        )
        n_kes: int = field(
            metadata={
                "name": "n-kes",
                "type": "Attribute",
            }
        )
        n_clusters: int = field(
            metadata={
                "name": "n-clusters",
                "type": "Attribute",
            }
        )
        n_accepted: int = field(
            metadata={
                "name": "n-accepted",
                "type": "Attribute",
            }
        )
        min_support: None | int = field(
            default=None,
            metadata={
                "name": "min-support",
                "type": "Attribute",
            },
        )
        min_support_ensemble: int = field(
            metadata={
                "name": "min-support-ensemble",
                "type": "Attribute",
            }
        )
        tau: None | float = field(
            default=None,
            metadata={
                "type": "Attribute",
            },
        )
        embedding: None | str = field(
            default=None,
            metadata={
                "type": "Attribute",
            },
        )

        class SupportHistogram(BaseModel):
            model_config = ConfigDict(defer_build=True)
            bin: list[KecConsensus.Summary.SupportHistogram.Bin] = field(
                default_factory=list,
                metadata={
                    "type": "Element",
                },
            )

            class Bin(BaseModel):
                model_config = ConfigDict(defer_build=True)
                support: int = field(
                    metadata={
                        "type": "Attribute",
                    }
                )
                clusters: int = field(
                    metadata={
                        "type": "Attribute",
                    }
                )

    class Terms(BaseModel):
        model_config = ConfigDict(defer_build=True)
        term: list[KecConsensus.Terms.Term] = field(
            default_factory=list,
            metadata={
                "type": "Element",
            },
        )

        class Term(BaseModel):
            model_config = ConfigDict(defer_build=True)
            name: str = field(
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
            kind: TermKindEnum = field(
                metadata={
                    "type": "Attribute",
                }
            )
            source: None | str = field(
                default=None,
                metadata={
                    "type": "Attribute",
                },
            )
            source_id: None | str = field(
                default=None,
                metadata={
                    "name": "source-id",
                    "type": "Attribute",
                },
            )
            iri: None | str = field(
                default=None,
                metadata={
                    "type": "Attribute",
                },
            )

    class KeyEventResult(BaseModel):
        model_config = ConfigDict(defer_build=True)
        curated_triple: list[KecConsensus.KeyEventResult.CuratedTriple] = (
            field(
                default_factory=list,
                metadata={
                    "name": "curated-triple",
                    "type": "Element",
                },
            )
        )
        ke_id: str = field(
            metadata={
                "name": "ke-id",
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )
        n_curated: int = field(
            metadata={
                "name": "n-curated",
                "type": "Attribute",
            }
        )
        n_confirmed: int = field(
            metadata={
                "name": "n-confirmed",
                "type": "Attribute",
            }
        )
        n_novel: int = field(
            metadata={
                "name": "n-novel",
                "type": "Attribute",
            }
        )

        class CuratedTriple(BaseModel):
            model_config = ConfigDict(defer_build=True)
            index: int = field(
                metadata={
                    "type": "Attribute",
                }
            )
            status: CuratedStatusEnum = field(
                metadata={
                    "type": "Attribute",
                }
            )
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
            action_id: None | str = field(
                default=None,
                metadata={
                    "name": "action-id",
                    "type": "Attribute",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                },
            )

    class ConsensusTriple(BaseModel):
        model_config = ConfigDict(defer_build=True)
        member: list[KecConsensus.ConsensusTriple.Member] = field(
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
        ke_id: str = field(
            metadata={
                "name": "ke-id",
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            }
        )
        status: ClusterStatusEnum = field(
            metadata={
                "type": "Attribute",
            }
        )
        support: int = field(
            metadata={
                "type": "Attribute",
            }
        )
        models: list[str] = field(
            default_factory=list,
            metadata={
                "type": "Attribute",
                "tokens": True,
            },
        )
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
        action_id: None | str = field(
            default=None,
            metadata={
                "name": "action-id",
                "type": "Attribute",
                "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
            },
        )
        curated_indices: list[int] = field(
            default_factory=list,
            metadata={
                "name": "curated-indices",
                "type": "Attribute",
                "tokens": True,
            },
        )
        method: None | str = field(
            default=None,
            metadata={
                "type": "Attribute",
            },
        )
        score: None | float = field(
            default=None,
            metadata={
                "type": "Attribute",
            },
        )

        class Member(BaseModel):
            model_config = ConfigDict(defer_build=True)
            model: str = field(
                metadata={
                    "type": "Attribute",
                }
            )
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
            action_id: None | str = field(
                default=None,
                metadata={
                    "name": "action-id",
                    "type": "Attribute",
                    "pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}",
                },
            )
            matched: None | bool = field(
                default=None,
                metadata={
                    "type": "Attribute",
                },
            )
            curated_index: None | int = field(
                default=None,
                metadata={
                    "name": "curated-index",
                    "type": "Attribute",
                },
            )
            method: None | str = field(
                default=None,
                metadata={
                    "type": "Attribute",
                },
            )
            score: None | float = field(
                default=None,
                metadata={
                    "type": "Attribute",
                },
            )

    class Lookup(BaseModel):
        model_config = ConfigDict(defer_build=True)
        key_event_reference: list[KecConsensus.Lookup.KeyEventReference] = (
            field(
                default_factory=list,
                metadata={
                    "name": "key-event-reference",
                    "type": "Element",
                },
            )
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
            title: None | str = field(
                default=None,
                metadata={
                    "type": "Attribute",
                },
            )
