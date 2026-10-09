# Agentic CAD reference guide

This guide covers the 12 research papers and research notes found in `REFERECES/` (the folder name is retained as it exists in the project). It summarizes each item, its implementation repository status, and how it can inform AgenticCAD. Repository availability is about a public implementation, not merely a paper, preprint, or project website. Status was checked against the documents and public links available on 9 October 2026.

## Quick orientation

| Source | Type | Public implementation repository? | Agentic CAD relevance |
|---|---|---|---|
| [ArtiCAD paper](REFERECES/Articad--Articulated%20CAD%20assebly%20Design%20via%20multi-angent%20code%20generation.pdf) | Research paper | No implementation link confirmed; project page says code “To be continued” | High for multi-part articulated assemblies |
| [ArtiCAD project page export](REFERECES/ArtiCAD%20_%20Project%20Page.pdf) | Project-page PDF export for ArtiCAD | No implementation link confirmed | High, companion overview of the same work |
| [Articraft](REFERECES/Articraft.pdf) | Research paper | Yes: [articraftresearch/Articraft](https://github.com/articraftresearch/Articraft) | High for structured asset generation and validation harnesses |
| [CAD-Assistant](REFERECES/CADAssistant__ICCV_2025.pdf) | ICCV 2025 paper | Yes: [dimitrismallis/CAD-Assistant](https://github.com/dimitrismallis/CAD-Assistant) | High for multimodal tool-using CAD and iterative editing |
| [PACT](REFERECES/PACT---Pruning%20and%20Clustering-Based%20Token%20Reduction%20for%20Faster%20Visual.pdf) | Research paper / preprint | Yes: [orailix/PACT](https://github.com/orailix/PACT) | Medium for reducing visual inference cost in image-conditioned CAD |
| [MEDA](REFERECES/MEDA_final.pdf) | Research paper (IDETC 2025) | Yes: [AnK-Accelerated-Komputing/MEDA](https://github.com/AnK-Accelerated-Komputing/MEDA) | High for planner/coder/executor/reviewer decomposition |
| [Memory-Augmented RL Agent for CAD](REFERECES/Memory-Augmented%20Reinforcement%20Learning%20Agent%20for%20CAD.pdf) | Research paper / preprint | No public implementation repository identified | High for learning from past jobs and reusable CAD skills |
| [Physics-in-the-Loop](REFERECES/Physics-in-the-Loop--%20A%20Hybrid%20Agentic%20Architecture%20for%20Validated%20CAD.pdf) | Research paper / preprint | No public implementation repository identified; paper says code/data will be released | High for engineering validation beyond shape similarity |
| [Agentic CAD Research Overview](REFERECES/Agentic%20CAD%20Research%20Overview.md) | Research landscape synthesis | Not applicable; it is a survey note, not a software project | High as a map of system patterns; verify its cited claims |
| [Automated URDF Validation Tools](REFERECES/Automated%20URDF%20Validation%20Tools.md) | Technical synthesis / proposed validation workflow | Not applicable; no single associated repository | High if AgenticCAD targets articulated robotics assets |
| [Comparing FreeCAD, CadQuery, and build123d](REFERECES/Comparing%20FreeCAD%20CadQuery%20and%20build123d.md) | Technical comparison | Not applicable; discusses public CAD libraries | High for choosing an execution backend |
| [Open-Source AI CAD Models](REFERECES/Open%20Source%20AI%20CAD%20Models.md) | Model and project landscape synthesis | Not applicable; lists repositories for individual projects | Medium-high for selecting complementary models and baselines |
| [FreeCAD headless scripting report (R1/R2)](REFERECES/freecad_headless_scripting_research_report.md) | Deep-research report, **corrected 2026-10-09** against FreeCAD source | Not applicable | High: Assembly joint and Sketcher diagnostic APIs. Read the corrected sections; items marked [UNVERIFIED] still need Phase 0 tests |
| [FreeCAD URDF Exporters Comparison (R3)](REFERECES/FreeCAD%20URDF%20Exporters%20Comparison.md) | Deep-research report, licenses verified 2026-10-09 | CROSS and RobotCAD (both LGPL-2.1) | High for ROS 2 package conventions |
| [Articraft architecture (R4)](REFERECES/articraft_architecture.md) | Repository architecture note | `mattzh72/articraft` (Apache-2.0), superseded by `articraftresearch/Articraft` | High for harness and SDK structure |

## Paper and project notes

### 1. ArtiCAD: Articulated CAD Assembly Design via Multi-Agent Code Generation

**What it is about.** A training-free four-agent pipeline (Design, Generation, Assembly, Review) that creates editable articulated assemblies from text or images. It plans part-to-part connectors and joint parameters before geometry generation, validates both geometry and assembly, supports rollback across stages, and exports URDF. The paper evaluates on ArtiCAD-Bench, CADPrompt, and ACD.

**Repository.** The paper’s project page is [articad.github.io](https://articad.github.io/), but the project-page PDF included here labels the code link “To be continued.” The [GitHub Pages source repository](https://github.com/shui-yuan/articad) hosts the project website; it should not be treated as the released implementation. **No public implementation repository confirmed.**

**Relevance to AgenticCAD: High.** This is directly useful if AgenticCAD needs assemblies rather than isolated solids. Its early connector planning, typed joints, per-component generation, staged validation, and rollback offer concrete architecture patterns. URDF export also connects CAD generation to robotics simulation.

### 2. ArtiCAD project page (PDF export)

**What it is about.** A 10-page export of the ArtiCAD project site. It presents the same system and example tasks as the paper, including articulated and industrial assemblies, agent stages, project results, and URDF export. It is a companion presentation, not a second independent method.

**Repository.** Same status as the paper: the site links code as “To be continued”; its [GitHub Pages repository](https://github.com/shui-yuan/articad) is the website source, not a confirmed implementation release.

**Relevance to AgenticCAD: High.** Useful for understanding the intended user workflow and visual examples; use the paper for technical detail and avoid double-counting it as separate evidence.

### 3. Articraft: An Agentic System for Scalable Articulated 3D Asset Generation

**What it is about.** The paper turns articulated 3D asset creation into program synthesis. An LLM writes code against a domain-specific SDK for parts, geometry, joints, and tests; a restricted harness compiles and validates outputs and returns structured feedback. The authors use the system to create Articraft-10K, a dataset of over 10,000 assets in 245 categories, with uses in robotics simulation and VR.

**Repository.** Yes: [articraftresearch/Articraft](https://github.com/articraftresearch/Articraft). The paper’s original project page says development moved to this repository. The implementation and paper may evolve independently, so check the repository’s current documentation when reproducing results.

**Relevance to AgenticCAD: High.** Strong reference for a constrained CAD SDK, sandboxed agent workspace, structured validation feedback, and generation of semantically articulated outputs. Its focus is scalable articulated assets rather than general mechanical CAD authoring.

### 4. CAD-Assistant: Tool-Augmented VLLMs as Generic CAD Task Solvers

**What it is about.** A multimodal vision-language model operates FreeCAD through Python and CAD-specific tools. It handles text, sketches, drawn commands, and 3D scans; iteratively executes commands against evolving CAD state; and uses tools for sketch parameterization, rendering, cross-sections, and related operations. The paper reports benchmark results for several CAD tasks and demonstrates broader workflows.

**Repository.** Yes: [dimitrismallis/CAD-Assistant](https://github.com/dimitrismallis/CAD-Assistant). The repository is public; its README describes released tools and an extensible tool registry.

**Relevance to AgenticCAD: High.** Useful for multimodal input, tool-based operation, state-aware command execution, and extending an agent with CAD-specific capabilities. It complements a code-generation backend with direct interactive editing and analysis workflows.

### 5. PACT: Pruning and Clustering-Based Token Reduction for Faster Visual Language Models

**What it is about.** PACT reduces the inference cost of vision-language models by pruning low-importance visual tokens and clustering/merging redundant tokens early in the language model. Its token-importance metric does not require attention scores, and its Distance Bounded Density Peak Clustering (DBDPC) limits how dissimilar tokens within a cluster can be. It is inference-time only and requires no additional training. The paper reports 50% visual-token reduction with negligible performance loss for LLaVA-OneVision-7B, and 71.3% reduction with a 1.4% performance drop at a higher reduction setting.

**Repository.** Yes: [orailix/PACT](https://github.com/orailix/PACT), linked by the paper as the codebase used for its results.

**Relevance to AgenticCAD: Medium.** This is not a CAD-generation or geometry paper, but it may help lower latency and memory use if AgenticCAD sends rendered CAD views or reference images to a VLM. It is most relevant to the vision component of an agent loop; evaluate whether token reduction preserves small features, dimensions, and visual defects that CAD review depends on.

### 6. MEDA: A Multi-Agent System for Parametric CAD Model Creation

**What it is about.** A CadQuery-based multi-agent pipeline divides work among design planning, script writing, execution, and review. Execution errors return to the coding/planning stages, while successful geometry is inspected by a CAD image reviewer. The paper evaluates 200 prompts and reports 99% script execution success and lower median point-cloud distance than the compared method.

**Repository.** Yes: [AnK-Accelerated-Komputing/MEDA](https://github.com/AnK-Accelerated-Komputing/MEDA). The repository includes application code and identifies the IDETC 2025 paper.

**Relevance to AgenticCAD: High.** A close architectural baseline for a CAD agent built around plan → code → execute → review. Its public implementation can inform practical CadQuery integration and failure routing. Treat its reported numbers as paper-specific because benchmark setup and model choice affect comparisons.

### 7. Memory-Augmented Reinforcement Learning Agent for CAD Generation

**What it is about.** A CAD generation agent uses a geometric-kernel toolchain and a closed loop for intent understanding, planning, execution, and verification. It maintains two forms of memory: successful cases and reusable skills. Reinforcement learning updates memory retrieval utility and agent policy, aiming to avoid retrieving examples that sound similar but are geometrically infeasible.

**Repository.** The paper is available as [arXiv:2605.19748](https://arxiv.org/abs/2605.19748), but no public implementation repository was identified in the paper or the repository search conducted for this guide.

**Relevance to AgenticCAD: High.** Relevant to persistent experience across tasks, reusable feature-level skills, and retrieval based on engineering usefulness rather than text similarity alone. The paper’s claims would be easier to assess with a released implementation and benchmark details.

### 8. Physics-in-the-Loop: A Hybrid Agentic Architecture for Validated CAD Engineering Design

**What it is about.** A closed-loop design system uses specialized agents to plan, generate, simulate, and revise CAD under load cases. It integrates validated engineering tools into the agent loop and introduces a benchmark and functional-validity metrics, addressing structural feasibility that visual similarity alone cannot measure.

**Repository.** The paper is available as [arXiv:2605.19717](https://arxiv.org/abs/2605.19717). It states that code, prompts, and dataset “will be made publicly available”; no implementation repository was identified during this review. The paper references [torch-fem](https://github.com/meyer-nils/torch-fem), but that is a FEM library, not the paper’s agent implementation.

**Relevance to AgenticCAD: High.** Important if generated designs must meet load, stress, displacement, or other engineering constraints. Its key lesson is to make simulation results structured feedback for revision and to grade functional validity separately from geometric similarity.

## Research-note summaries

### 9. Agentic CAD Research Overview

**What it is about.** A broad synthesis of agentic CAD patterns: role-specialized agents, execution and visual feedback loops, persistent interactive environments, physics-aware refinement, articulated assemblies, solver-aided DSLs, benchmarks, and geometric-consensus selection. It also compares systems such as CADSmith, AgenticCADedit, AgenticTCAD, AIDL, and CAD-Assistant.

**Repository.** Not applicable. This is a Markdown literature synthesis, not a software implementation. It contains references to outside projects, but does not establish a repository for the note itself.

**Relevance to AgenticCAD: High as a roadmap.** Use it to identify capabilities and research questions. It includes many specific performance claims and project details; verify those against the original papers before using them as engineering requirements or reporting them as established results.

### 10. Automated URDF Validation Tools

**What it is about.** A proposed multi-stage validation architecture for generated robot descriptions: XML/schema and tree checks; joint and inertia feasibility; collision-mesh processing; then simulator compilation, gravity-drop, and actuation tests. It compares Pinocchio, MuJoCo, Isaac Sim/Lab, PyBullet, and Gazebo as validation backends and describes how to return diagnostics to an agent.

**Repository.** Not applicable as a single project. It discusses third-party tools, including [Pinocchio](https://github.com/stack-of-tasks/pinocchio), [MuJoCo](https://github.com/google-deepmind/mujoco), and [CoACD](https://github.com/SarahWeiii/CoACD); these are dependencies and examples, not a repository for this note.

**Relevance to AgenticCAD: High for robotics output.** Useful as a checklist for validating URDF-like exports beyond XML parsing. It is a technical synthesis, not a validated comparative study; confirm simulator-specific behavior and requirements in each tool’s official documentation before implementing gates.

### 11. Comparing FreeCAD, CadQuery, and build123d

**What it is about.** A technical comparison of three Python-accessible, Open CASCADE-based CAD environments. It describes their modeling APIs and state/history models, headless execution considerations, selectors and error modes, and tradeoffs for automated and agentic workflows.

**Repository.** Not applicable to the comparison document. The underlying projects are public: [FreeCAD](https://github.com/FreeCAD/FreeCAD), [CadQuery](https://github.com/CadQuery/cadquery), and [build123d](https://github.com/gumyr/build123d).

**Relevance to AgenticCAD: High.** Directly informs backend choice. CadQuery/build123d suit code-first workflows; FreeCAD offers a broad application API and interactive feature tree. The right choice depends on AgenticCAD’s need for editability, headless reliability, multi-step state, and downstream file compatibility. Treat the note’s version-sensitive comparisons as hypotheses to check against the installed versions.

### 12. Open-Source AI CAD Models

**What it is about.** A survey of open-source CAD generation approaches, grouped around direct B-Rep synthesis, parametric construction sequences, and code-generating agents. It includes model/project comparisons and robotics-oriented systems such as ArtiCAD and Articraft, with a focus on output representation, input modality, kernel, and URDF capability.

**Repository.** Not applicable to the survey itself. It lists public implementations for some projects, including [BrepGen](https://github.com/samxuxiang/BrepGen), [DTGBrepGen](https://github.com/jinli99/DTGBrepGen), [SkexGen](https://github.com/samxuxiang/SkexGen), and [CAD-Coder](https://github.com/anniedoris/CAD-Coder). Confirm each repository is the official implementation before depending on it; the note’s ArtiCAD entry should be read alongside the code-availability caveat above.

**Relevance to AgenticCAD: Medium-high.** Useful for choosing baselines or complementary generation models and for understanding the difference between static generated geometry and editable programmatic CAD. It is a broad, fast-changing catalog rather than an evaluated benchmark.

## How these references map to AgenticCAD

- **Agent orchestration and repair:** MEDA, CAD-Assistant, and ArtiCAD describe task decomposition, tool execution, error feedback, and review.
- **CAD kernel and authoring interface:** the FreeCAD/CadQuery/build123d comparison helps select a code execution backend; CAD-Assistant shows FreeCAD tool use and MEDA shows a CadQuery workflow.
- **Persistent experience:** the memory-augmented RL paper motivates case memory and reusable skills with utility-aware retrieval.
- **Engineering correctness:** Physics-in-the-Loop motivates simulation-based functional checks; the URDF note adds structural and dynamic validation for robotics.
- **Articulated output and datasets:** ArtiCAD and Articraft cover parts, joints, kinematic structure, and simulation-ready asset generation.
- **Research positioning:** the overview and open-source model survey identify adjacent methods, but their detailed claims should be traced back to primary papers before reuse.

## Folder items excluded

`REFERECES/ChatGPT Installer.exe` and `REFERECES/Microsoft.Services.Store.winmd` are Windows application/package artifacts, not papers or research documents about agentic CAD, so they are not summarized above.
