# ContextBubble Final-Project Evaluation

Demo video: `fNk_zzaMoSs`  
Fixture: `backend/fixtures/fNk_zzaMoSs.vtt`  
Mode: manual review of candidate bubbles from the fixture-backed demo path

Deterministic validation complements model review by enforcing grounded segment
IDs, exact anchor timestamps, length caps, confidence bounds, duplicate removal,
and minimum spacing after the Concept Agent and Reviewer Agent finish.

| # | Segment | Candidate | Model decision | Reviewer decision | Validator result | Grounding | Correctness | Timestamp | Learner fit | Concision | Duplicate | Final decision | Human note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | segment-001 | timestamped segments | proposed | accepted | accepted | pass | pass | useful | pass | pass | unique | accepted | Clear setup concept. |
| 2 | segment-001 | question answering assistant | proposed | accepted | accepted | pass | pass | useful | pass | pass | unique | accepted | Useful product framing. |
| 3 | segment-001 | transcript splitting | proposed | revised | accepted | pass | pass | useful | pass | pass | related | accepted | Reviewer should prefer concise wording. |
| 4 | segment-001 | unreliable timestamps | proposed | rejected | rejected | fail | mixed | weak | mixed | pass | unique | rejected | Not stated in transcript. |
| 5 | segment-002 | embeddings | proposed | accepted | accepted | pass | pass | useful | pass | pass | unique | accepted | Core concept. |
| 6 | segment-002 | numeric representations | proposed | accepted | accepted | pass | pass | useful | pass | pass | related | accepted | Grounded but close to embeddings. |
| 7 | segment-002 | compare meaning | proposed | revised | accepted | pass | pass | useful | pass | pass | unique | accepted | Good beginner wording after revision. |
| 8 | segment-002 | neural network training | proposed | rejected | rejected | fail | fail | weak | fail | pass | unique | rejected | Transcript does not mention training. |
| 9 | segment-003 | vector database | proposed | accepted | accepted | pass | pass | useful | pass | pass | unique | accepted | Grounded retrieval concept. |
| 10 | segment-003 | closest segments | proposed | revised | accepted | pass | pass | useful | pass | pass | related | accepted | Reviewer can tie to retrieval. |
| 11 | segment-003 | viewer question | proposed | accepted | accepted | pass | pass | useful | pass | pass | unique | accepted | Explains why search matters. |
| 12 | segment-003 | SQL database | proposed | rejected | rejected | fail | fail | weak | mixed | pass | unique | rejected | Wrong database type. |
| 13 | segment-004 | cosine similarity | proposed | accepted | accepted | pass | pass | useful | pass | pass | unique | accepted | Strong timestamp anchor. |
| 14 | segment-004 | vector direction | proposed | accepted | accepted | pass | pass | useful | pass | pass | related | accepted | Good expansion candidate. |
| 15 | segment-004 | vector magnitude | proposed | rejected | rejected | mixed | mixed | weak | mixed | pass | unique | rejected | Transcript emphasizes direction. |
| 16 | segment-005 | retrieval augmented generation | proposed | accepted | accepted | pass | pass | useful | pass | pass | unique | accepted | Key RAG concept. |
| 17 | segment-005 | retrieves relevant context | proposed | accepted | accepted | pass | pass | useful | pass | pass | related | accepted | Clear process step. |
| 18 | segment-005 | writes an answer using evidence | proposed | revised | accepted | pass | pass | useful | pass | pass | unique | accepted | Useful after shortening. |
| 19 | segment-006 | reviewer stage | proposed | accepted | accepted | pass | pass | useful | pass | pass | unique | accepted | Shows agentic workflow. |
| 20 | segment-006 | learner level fit | proposed | accepted | accepted | pass | pass | useful | pass | pass | unique | accepted | Supports final-project rubric. |
