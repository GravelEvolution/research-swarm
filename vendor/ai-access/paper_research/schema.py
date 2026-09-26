# -*- coding: utf-8 -*-
"""SQLite schema (layered decision library)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS SrcRecord (
  SourceID     INTEGER PRIMARY KEY AUTOINCREMENT,
  PaperID      INTEGER NOT NULL,
  SourceType   TEXT NOT NULL,
  ExternalId   TEXT,
  URL          TEXT,
  RawJson      TEXT,
  FetchedAt    TEXT
);
CREATE INDEX IF NOT EXISTS ix_Src_Paper ON SrcRecord(PaperID);

CREATE TABLE IF NOT EXISTS Evidence (
  EvidenceID   INTEGER PRIMARY KEY AUTOINCREMENT,
  PaperID      INTEGER NOT NULL,
  EvType       TEXT NOT NULL,
  QuoteText    TEXT NOT NULL,
  Locator      TEXT,
  Extractor    TEXT DEFAULT 'AI',
  Confidence   REAL DEFAULT 0.5,
  CreatedAt    TEXT
);
CREATE INDEX IF NOT EXISTS ix_Ev_Paper ON Evidence(PaperID);

CREATE TABLE IF NOT EXISTS Fact (
  FactID       INTEGER PRIMARY KEY AUTOINCREMENT,
  PaperID      INTEGER NOT NULL,
  FactType     TEXT NOT NULL,
  Content      TEXT,
  ValueNum     REAL,
  Unit         TEXT,
  EvidenceID   INTEGER,
  ParentFactID INTEGER,
  CreatedAt    TEXT
);
CREATE INDEX IF NOT EXISTS ix_Fact_Paper ON Fact(PaperID);

CREATE TABLE IF NOT EXISTS Paper (
  PaperID              INTEGER PRIMARY KEY AUTOINCREMENT,
  Title                TEXT NOT NULL,
  Abstract             TEXT,
  TLDR                 TEXT,
  Year                 INTEGER,
  Venue                TEXT,
  DOI                  TEXT,
  ArxivId              TEXT,
  S2PaperId            TEXT,
  OpenAlexId           TEXT,
  Authors              TEXT,
  CodeURL              TEXT,
  PDFPath              TEXT,
  CitationCount        INTEGER DEFAULT 0,
  InfluentialCitations INTEGER DEFAULT 0,
  Publisher            TEXT,
  ImportedAt           TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_Paper_OpenAlex ON Paper(OpenAlexId);
CREATE UNIQUE INDEX IF NOT EXISTS ix_Paper_DOI ON Paper(DOI);

CREATE TABLE IF NOT EXISTS ResearchTopic (
  TopicID          INTEGER PRIMARY KEY AUTOINCREMENT,
  TopicName        TEXT NOT NULL,
  Description      TEXT,
  ParentTopicID    INTEGER,
  Keywords         TEXT,
  SuccessCriteria  TEXT,
  Priority         INTEGER DEFAULT 3,
  Status           TEXT DEFAULT 'active',
  LastScanAt       TEXT
);

CREATE TABLE IF NOT EXISTS PaperTopic (
  PaperID       INTEGER NOT NULL,
  TopicID       INTEGER NOT NULL,
  Relevance     REAL DEFAULT 0.5,
  MatchReason   TEXT,
  SubDirection  TEXT,
  CreatedAt     TEXT,
  PRIMARY KEY (PaperID, TopicID)
);

CREATE TABLE IF NOT EXISTS Facet (
  FacetID        INTEGER PRIMARY KEY AUTOINCREMENT,
  FacetName      TEXT NOT NULL,
  FacetTopology  TEXT NOT NULL CHECK (FacetTopology IN ('Tree','Network','Relevance')),
  DecisionRole   TEXT,
  SortOrder      INTEGER DEFAULT 0,
  Description    TEXT
);

CREATE TABLE IF NOT EXISTS FacetNode (
  NodeID              INTEGER PRIMARY KEY AUTOINCREMENT,
  FacetID             INTEGER NOT NULL,
  NodeName            TEXT NOT NULL,
  Description         TEXT,
  ParentNodeID        INTEGER,
  NodeLevel           INTEGER DEFAULT 0,
  Path                TEXT,
  NodeKind            TEXT DEFAULT 'problem',
  IsSearchRoot        INTEGER DEFAULT 0,
  SearchRootSince     TEXT,
  PromotedFromParentID INTEGER,
  CanPromote          INTEGER DEFAULT 1,
  Status              TEXT DEFAULT 'active',
  Priority            INTEGER DEFAULT 3,
  TopicID             INTEGER,
  CreatedAt           TEXT
);
CREATE INDEX IF NOT EXISTS ix_Node_Facet ON FacetNode(FacetID);
CREATE INDEX IF NOT EXISTS ix_Node_Parent ON FacetNode(ParentNodeID);
CREATE INDEX IF NOT EXISTS ix_Node_Path ON FacetNode(Path);

CREATE TABLE IF NOT EXISTS PaperFacet (
  PaperFacetID     INTEGER PRIMARY KEY AUTOINCREMENT,
  PaperID          INTEGER NOT NULL,
  NodeID           INTEGER NOT NULL,
  FactID           INTEGER,
  EvidenceID       INTEGER,
  Confidence       REAL DEFAULT 0.5,
  IsHumanConfirmed INTEGER DEFAULT 0,
  CreatedAt        TEXT,
  UNIQUE (PaperID, NodeID)
);

CREATE TABLE IF NOT EXISTS NodeInheritance (
  InheritID      INTEGER PRIMARY KEY AUTOINCREMENT,
  ChildNodeID    INTEGER NOT NULL,
  ParentNodeID   INTEGER NOT NULL,
  InheritedField TEXT NOT NULL,
  InheritedValue TEXT,
  IsOverridden   INTEGER DEFAULT 0,
  OverrideValue  TEXT,
  OverrideReason TEXT
);

CREATE TABLE IF NOT EXISTS NodeOpLog (
  OpID          INTEGER PRIMARY KEY AUTOINCREMENT,
  NodeID        INTEGER NOT NULL,
  OpType        TEXT NOT NULL,
  PayloadJson   TEXT,
  Reason        TEXT,
  Actor         TEXT DEFAULT 'AI',
  RelatedNodeID INTEGER,
  OpAt          TEXT
);

CREATE TABLE IF NOT EXISTS NodePromotion (
  PromotionID      INTEGER PRIMARY KEY AUTOINCREMENT,
  NodeID           INTEGER NOT NULL,
  FromParentNodeID INTEGER,
  TriggerReason    TEXT,
  InheritedBrief   TEXT,
  ScanRunID        INTEGER,
  PromotedAt       TEXT
);

CREATE TABLE IF NOT EXISTS RelationType (
  RelTypeID   INTEGER PRIMARY KEY AUTOINCREMENT,
  TypeName    TEXT NOT NULL,
  IsDirected  INTEGER DEFAULT 1,
  FacetID     INTEGER,
  Description TEXT
);

CREATE TABLE IF NOT EXISTS RelEdge (
  RelationID   INTEGER PRIMARY KEY AUTOINCREMENT,
  RelTypeID    INTEGER NOT NULL,
  FromPaperID  INTEGER,
  FromNodeID   INTEGER,
  ToPaperID    INTEGER,
  ToNodeID     INTEGER,
  Weight       REAL DEFAULT 0.5,
  EvidenceID   INTEGER,
  Confidence   REAL DEFAULT 0.5,
  CreatedAt    TEXT
);

CREATE TABLE IF NOT EXISTS Association (
  AssocID       INTEGER PRIMARY KEY AUTOINCREMENT,
  Dim           TEXT NOT NULL,
  SourceRefType TEXT NOT NULL,
  SourceRefID   INTEGER NOT NULL,
  TargetRefType TEXT NOT NULL,
  TargetRefID   INTEGER NOT NULL,
  Degree        REAL NOT NULL,
  Method        TEXT,
  EvidenceID    INTEGER,
  CreatedAt     TEXT
);

CREATE TABLE IF NOT EXISTS SolutionCluster (
  ClusterID    INTEGER PRIMARY KEY AUTOINCREMENT,
  NodeID       INTEGER NOT NULL,
  ApproachName TEXT NOT NULL,
  Description  TEXT,
  AvgScore     REAL DEFAULT 0,
  PaperCount   INTEGER DEFAULT 0,
  Status       TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS PaperSolution (
  ClusterID      INTEGER NOT NULL,
  PaperID        INTEGER NOT NULL,
  RankInSolution INTEGER DEFAULT 1,
  IsHighValue    INTEGER DEFAULT 0,
  PRIMARY KEY (ClusterID, PaperID)
);

CREATE TABLE IF NOT EXISTS DecisionCard (
  CardID             INTEGER PRIMARY KEY AUTOINCREMENT,
  PaperID            INTEGER NOT NULL UNIQUE,
  OneLinePosition    TEXT,
  SolveWhat          TEXT,
  NoveltyScore       INTEGER DEFAULT 3,
  RelevanceScore     INTEGER DEFAULT 3,
  ImpactScore        INTEGER DEFAULT 3,
  ReproValue         INTEGER DEFAULT 3,
  Urgency            INTEGER DEFAULT 3,
  OverallScore       REAL DEFAULT 3,
  ImpactRelation     TEXT DEFAULT 'unrelated',
  ImpactNote         TEXT,
  KeyAdvantage       TEXT,
  KeyLimitation      TEXT,
  DecisionConfidence REAL DEFAULT 0.5,
  IsHumanReviewed    INTEGER DEFAULT 0,
  DecidedAt          TEXT
);

CREATE TABLE IF NOT EXISTS ScoreBasis (
  BasisID       INTEGER PRIMARY KEY AUTOINCREMENT,
  CardID        INTEGER NOT NULL,
  ScoreField    TEXT NOT NULL,
  FactID        INTEGER,
  AssociationID INTEGER,
  EvidenceID    INTEGER,
  Weight        REAL DEFAULT 1.0,
  Note          TEXT
);

CREATE TABLE IF NOT EXISTS Comparison (
  CompareID     INTEGER PRIMARY KEY AUTOINCREMENT,
  CardID        INTEGER NOT NULL,
  BaselineName  TEXT NOT NULL,
  MetricName    TEXT,
  DeltaText     TEXT,
  CostTradeoff  TEXT,
  EvidenceID    INTEGER
);

CREATE TABLE IF NOT EXISTS EvidenceFlag (
  FlagID           INTEGER PRIMARY KEY AUTOINCREMENT,
  CardID           INTEGER NOT NULL UNIQUE,
  HasCode          INTEGER DEFAULT 0,
  HasOfficialRepo  INTEGER DEFAULT 0,
  HasAblation      INTEGER DEFAULT 0,
  HasOpenData      INTEGER DEFAULT 0,
  BenchmarkList    TEXT,
  EvidenceGap      TEXT,
  ReproDifficulty  INTEGER DEFAULT 3,
  EstimatedEffort  TEXT
);

CREATE TABLE IF NOT EXISTS ActionItem (
  ActionID   INTEGER PRIMARY KEY AUTOINCREMENT,
  CardID     INTEGER NOT NULL,
  ActionType TEXT,
  ActionText TEXT,
  Priority   INTEGER DEFAULT 3,
  Status     TEXT DEFAULT 'open',
  DueAt      TEXT
);

CREATE TABLE IF NOT EXISTS CitationSignal (
  SignalID         INTEGER PRIMARY KEY AUTOINCREMENT,
  PaperID          INTEGER NOT NULL,
  CitationCount    INTEGER DEFAULT 0,
  RecentCitations  INTEGER DEFAULT 0,
  TopCitingVenues  TEXT,
  CheckedAt        TEXT
);

CREATE TABLE IF NOT EXISTS ProtocolContract (
  ContractID       TEXT PRIMARY KEY,
  Version          TEXT NOT NULL,
  Name             TEXT NOT NULL,
  InputSchemaJson  TEXT,
  OutputSchemaJson TEXT,
  CallSignature    TEXT,
  IsActive         INTEGER DEFAULT 1,
  CreatedAt        TEXT
);

CREATE TABLE IF NOT EXISTS RetrievalTask (
  TaskID                INTEGER PRIMARY KEY AUTOINCREMENT,
  RootNodeID            INTEGER NOT NULL,
  ContractID            TEXT NOT NULL,
  UpstreamReqJson       TEXT,
  LocalOverrideJson     TEXT,
  Status                TEXT DEFAULT 'pending',
  InheritanceCompliant  INTEGER DEFAULT 0,
  InheritanceNote       TEXT,
  CreatedAt             TEXT,
  FinishedAt            TEXT
);

CREATE TABLE IF NOT EXISTS RankedResult (
  ResultID        INTEGER PRIMARY KEY AUTOINCREMENT,
  TaskID          INTEGER NOT NULL,
  PaperID         INTEGER NOT NULL,
  Rank            INTEGER NOT NULL,
  Score           REAL DEFAULT 0,
  MatchReason     TEXT,
  EvidenceID      INTEGER,
  SolutionApproach TEXT
);
CREATE INDEX IF NOT EXISTS ix_Result_TaskRank ON RankedResult(TaskID, Rank);

CREATE TABLE IF NOT EXISTS CandidateNode (
  CandID            INTEGER PRIMARY KEY AUTOINCREMENT,
  TaskID            INTEGER NOT NULL,
  Name              TEXT NOT NULL,
  Reason            TEXT,
  SuggestedParentID INTEGER,
  Status            TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS ScanRun (
  ScanID              INTEGER PRIMARY KEY AUTOINCREMENT,
  RootNodeID          INTEGER,
  OriginTopicID       INTEGER,
  QueryText           TEXT,
  InheritedQueryParts TEXT,
  ScanSource          TEXT,
  ScannedAt           TEXT,
  NewCount            INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ScanHit (
  ScanID    INTEGER NOT NULL,
  PaperID   INTEGER NOT NULL,
  IsNew     INTEGER DEFAULT 0,
  DeltaNote TEXT,
  PRIMARY KEY (ScanID, PaperID)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn
