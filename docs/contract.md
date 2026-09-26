# Shared contract v1

> Historical engine contract. The current task-scoped conversation API and autonomous UX are defined in conversation-contract.md. The gated engine API remains for compatibility tests only; it is not the current UI workflow. PDF extraction additionally uses pypdf.

All paths below are relative to outputs/research-swarm. Python uses only stdlib. Frontend React 18/TypeScript/Vite/@douyinfe/semi-ui + semi-icons. All UI text Chinese. No external shared service or DSH session.

## Library

`research_swarm.library.Library(source: str | Path)`; `.load() -> dict`; `.pdf_path(paper_id: str) -> Path | None`; `.retrieve(query: str, top_k: int=10) -> dict` invokes existing library CLI only when explicitly requested. load is read-only.

Library dict: `{sourcePath, sourceDb, topic:{id,title,description,successCriteria}, papers:Paper[], facetNodes:FacetNode[], evidence:Evidence[], relations:dict[], stats:dict}`.

Paper: `{id:string,title,abstract,year,venue,doi,authors,codeUrl,pdfAvailable:boolean,pdfPath:string|null,score:number (0..100),scores:{novelty,relevance,impact,reproducibility,urgency} (0..100),reason:string,reproducibility:string,workerStatus:'pending'|'running'|'completed'|'failed'|'waiting_user',facetNodeIds:string[],evidenceIds:string[],facts:dict[],scoreBasis:dict[],feedback:'interested'|'not_interested'|'read_later'|null}`. Reproducibility is human-readable materials status, not a claim of reproduced success.

FacetNode: `{id:string,parentId:string|null,title,facetId:string,facetName:string,topology:string,paperIds:string[]}`.
Evidence: `{id:string,paperId:string,quote:string,locator:string,type:string,confidence:number,extractor:string}`. Unknown locator is `未定位`. IDs are original numeric IDs stringified, NOT prefixes.

## Engine

`research_swarm.engine.Engine(library:dict, store_path:str|Path, runner:callable|None=None, max_workers:int=3)`; `snapshot()->dict` thread safe; `command(action:str,payload:dict)->dict`; `close()`.

Runner signature `runner(node:dict, context:dict, log:callable)->dict`. Context has `{library,requirements,children:dict[],mode:'evidence'|'llm',round:int}`; runner phase is `node.phase = 'plan'|'execute'|'aggregate'`. `log(message:str)` records visible structured logs. Return `{summary:string,evidenceIds:string[],claims:Claim[],structured:dict,children?:ChildTask[],unresolved?:string[]}`. Each ChildTask has `{title,description,acceptance,constraints,kind:'research'|'evidence'|'experiment',sourceNodeId?:string,requirementIds?:string[],children?:ChildTask[]}`. Engine limits decomposition depth (4) and total tasks (80) and validates hierarchy. A node with completed children calls aggregate, not plan again. Existing facet-node agents must all be present, inactive ones remain pending until a demand selects them.

Engine snapshot:
```
{revision:number,project:{id,title,description,round:number,sourcePath,mode},stage:number(0..8),paused:boolean,status:'idle'|'running'|'waiting_user'|'failed'|'completed',requirements:Requirement[],nodes:Node[],edges:Edge[],papers:Paper[],facetNodes:FacetNode[],evidence:Evidence[],checkpoints:Checkpoint[],activeCheckpointId:string|null,activities:Activity[],report:{summary:string,claims:Claim[],unresolved:string[],approved:boolean},history:dict[]}
```
Requirement `{id,description,acceptance,constraints,version:number,sourceNodeIds?:string[]}`.
Node `{id,parentId:string|null,title,role,kind,phase,status:'pending'|'running'|'completed'|'failed'|'waiting_user',progress:number,input:dict,output:dict|null,logs:Activity[],sourceNodeId:string|null,requirementIds:string[],evidenceIds:string[],startedAt:string|null,finishedAt:string|null,elapsedMs:number,version:number,active:boolean}`.
Edge `{source,target,type:'decompose'|'return'|'compare',reason,informational?:boolean}`. Snapshot derives one informational compare edge for each pair of active sibling tasks under the same parent. These display relations are not persisted execution dependencies and never cause sibling scheduling waits or cascading invalidation.
Activity `{id,at:ISO8601,actor:'AI'|'user'|'system',message,level?:string,nodeId?:string}`.
Claim `{id,text,evidenceIds:string[],nodeId?:string,status:'candidate'|'confirmed'|'rejected',limitations?:string}`. No evidence must stay explicitly unsupported.
Checkpoint `{id,type:'requirements'|'recommendations'|'comparison'|'final',status:'pending'|'confirmed'|'superseded',title,summary,createdAt,resolvedAt?:string,revision:number,decision?:string,userNote?:string}`.

Initial state has all imported data, draft requirements, facet agents plus central agent and pending requirements checkpoint at stage 0. Confirm requirements validates nonempty descriptions/acceptance and progresses through stored-library retrieval/structure to recommendations checkpoint stage 3. Confirm recommendations starts research root planning; tasks decompose/execute/aggregate; final root aggregate reaches comparison checkpoint stage 7. Confirm comparison sets candidate claims confirmed (unless rejected), creates final checkpoint stage 8. Confirm final marks completed and approved. Next round reopens requirements while keeping audit history. No auto-approval.

Commands:
- `requirements {requirements:Requirement[]}` saves draft; if already confirmed use intervention impact first (reject unsafe direct edits with readable ValueError).
- `checkpoint {id,decision:'confirm'|'modify'|'rollback',note?:string}`; modify stays paused, rollback goes to previous historical checkpoint (or same requirements initially).
- `pause {}` / `resume {}`; resume must not clear any pending checkpoint.
- `impact {nodeId:string,kind:'modify'|'insert'|'reject'|'deepen'}` returns `{revision,affectedIds:string[],downstreamCount:number}`. For modify/reject include descendants and aggregation consumers in affectedIds. For insert/deepen preserve existing children and invalidate the target's aggregation plus its consumers only. Count excludes source. Sibling branches untouched unless explicit dependency.
- `intervene {nodeId,kind,text:string,acceptance?:string,taskKind?:'research'|'evidence'|'experiment',expectedRevision:number,requirements?:Requirement[],experiment?:dict}` invalidates affected nodes/results and old in-flight work, records user cause, reruns affected branches; user gate remains enforced. Root edits with requirements reopen the requirements gate. Experiment input is user-supplied `{metric,lowerIsBetter,groups:{name:number[]}}`; real numerical execution creates traceable artifact evidence. Runner return may contain generatedEvidence, accepted only after stale-output guards.
- `rollback {checkpointId:string}` restores checkpoint snapshot with monotonic revision/epoch; keeps audit log/history, supersedes newer decisions; pending selected checkpoint; waits for user.
- `feedback {paperId:string,value:'interested'|'not_interested'|'read_later'|null}`
- `retry {nodeId:string}`
- `mode {mode:'evidence'|'llm'}` only while paused/idle, no pending model output accepted from earlier mode.
- `next-round {}`

Checkpoint and action mutations must serialize under lock. In-flight callbacks bind node.version + execution epoch; stale results discarded. Pause prevents scheduling; checkpoints cannot be bypassed. Persist to SQLite; crashed running nodes become pending and global pause on reopen. `close` stops workers safely.

## HTTP API (root implements)

GET `/api/state` -> engine snapshot; GET `/api/events` SSE event `change` with revision; GET `/api/health`.
POST `/api/actions/<action>` body payload -> engine command (usual snapshot, impact object for impact); errors `{error:string}` HTTP 400/409.
GET `/api/settings` -> `{mode,provider:{type,baseUrl,model,hasKey},sourcePath,capabilities:dict}`; POST `/api/settings` `{mode?,provider?:{type,baseUrl,model,apiKey?}}`; keys never returned.
POST `/api/provider/test` -> `{ok,message}`.
POST `/api/retrieve` `{query,topK}` -> explicit source retrieval then refresh; HTTP response may include result and snapshot. Disable while active work.
POST `/api/deepen` `{nodeId,text,acceptance?,query?,topK?,expectedRevision}` -> optional real external paper search scoped with the original source node, then atomic import and insertion of a research child at the selected node. New paper IDs are preserved in task input. New results return along the original chain. Long searches appear in additive `state.operations` and a persistent server journal. `refresh-library` is an internal-only engine action; client cannot inject source library data.
GET `/api/papers/<id>/pdf` only serves known source PDF. GET `/api/export` -> ZIP only when final approved. GET `/api/export?format=json` -> finalized snapshot.
Static built frontend served same origin. Dev API proxy to port 4381; production single Python port 4381.

Frontend use fetch + EventSource refresh plus polling fallback, every action awaited and error shown. Read state from server; no fake local progress. Settings via Drawer accessible sidebar footer. Buttons with meaningful accessible labels. Treat failures as failures; no silent demo fallback.
