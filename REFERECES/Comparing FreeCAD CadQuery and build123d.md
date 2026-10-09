# **Comparative Analysis of FreeCAD, CadQuery, and build123d in Programmatic CAD and Agentic AI Workflows**

## **1\. Introduction and Paradigm Shift in Code-CAD**

Computer-Aided Design (CAD) is undergoing a structural transition driven by the necessity for automation, version control, and programmatic geometry generation1. For decades, mechanical engineering design has been dominated by interactive, graphical user interface (GUI) software operating on parametric feature-based Boundary Representation (B-Rep) kernels. While GUI-driven CAD software provides an intuitive interface for manual conceptualization, it creates severe bottlenecks when integrating with automated hardware development pipelines, algorithmic parametric optimization, version-controlled repository workflows, and continuous integration testing2.  
To resolve these limitations, the paradigm of Code-CAD (or CAD-as-code) has emerged1. Code-CAD replaces manual geometric construction in a GUI with executable source code scripts that programmatically define shapes, construction histories, parameter relations, and topological operations1. By expressing geometric models as source code, mechanical designs can be managed using traditional software development practices, including version control with Git, automated regression testing, continuous generation of manufacturing artifacts, and dynamic integration with external configuration data1.  
At the foundation of industrial-grade Code-CAD lies the Open CASCADE Technology (OCCT) geometric kernel1. OCCT provides exact, analytic mathematical representations of complex shapes, surfaces, and topological boundaries comprising Vertices, Edges, Wires, Faces, Shells, and Solids1. This exact B-Rep modeling contrasts fundamentally with non-analytic, polygon mesh-based Constructive Solid Geometry (CSG) engines such as OpenSCAD2. While mesh-based CSG engines are accessible, their reliance on approximated planar triangular tessellations introduces compounding tolerance errors that render models unsuitable for tight-tolerance manufacturing processes like CNC machining or complex aerospace sub-assemblies1.  
The open-source B-Rep Code-CAD ecosystem is primarily defined by three frameworks: **FreeCAD**, **CadQuery**, and **build123d**1. Although all three frameworks ultimately rely on the OCCT geometric kernel (either directly or via Python bindings like OCP), they diverge in their API paradigms, architectural abstractions, state tracking, topological selector logic, and suitability for downstream programmatic execution1. Furthermore, recent advancements in artificial intelligence have expanded Code-CAD into **Agentic CAD**, where Large Language Models (LLMs) and autonomous agents serve as primary generative authors that write, execute, inspect, and self-repair CAD scripts through iterative feedback loops6.  
Evaluating the relative capabilities of FreeCAD, CadQuery, and build123d requires an examination of their core software architectures, topological selector mechanisms, execution performance profiles, developer experience, and their operational efficacy when deployed inside autonomous AI agent workflows1.

## **2\. Architectural Principles and API Design Paradigms**

Understanding the software architecture of each framework reveals how user scripts communicate with the underlying geometric kernel1. The computational stack across all three tools follows a layered hierarchy: user-facing script interfaces process inputs and state changes, translating high-level geometric operations down into lower-level C++ OCCT kernel invocations1.

Python  
\# FreeCAD Headless Scripting Pattern  
import FreeCAD as App  
import Part

doc \= App.newDocument("Mechanism")  
box\_obj \= doc.addObject("Part::Box", "BaseBox")  
box\_obj.Length, box\_obj.Width, box\_obj.Height \= 100.0, 100.0, 20.0  
doc.recompute()  
Part.export(\[box\_obj\], "output\_freecad.step")

FreeCAD is constructed as a monolithic desktop application written in C++ that embeds a comprehensive Python scripting binding layer4. Its architecture centers around a global Document Graph model (App::Document) containing discrete, parametric Document Objects (App::DocumentObject)11. Scripting in FreeCAD involves manipulating this feature tree, instantiating domain-specific workbench objects (such as Part::Box or Sketcher::SketchObject), establishing relative links, and explicitly triggering execution graph recomputations via doc.recompute()11.  
While FreeCAD can run interactively via its GUI, headless execution requires calling FreeCADCmd or binding the application's shared C++ libraries directly inside an external Python environment11. Because FreeCAD's Python layer was designed to support an interactive C++ application, headless automation entails setup overhead, environment path management (sys.path.append), and object-graph management to maintain state synchronization11.

Python  
\# CadQuery Fluent API Pattern  
import cadquery as cq

result \= (  
    cq.Workplane("XY")  
    .box(100.0, 100.0, 20.0)  
    .faces("\>Z")  
    .workplane()  
    .hole(10.0)  
)  
cq.exporters.export(result, "output\_cadquery.step")

CadQuery was developed to provide a lightweight, pure-Python abstraction layer over Open CASCADE without requiring the overhead of the FreeCAD application infrastructure4. CadQuery interfaces with OCCT through the OCP Python wrapper4. The primary architectural abstraction in CadQuery is its Fluent API, which centers on sequential method chaining anchored by a stateful Workplane object2.  
Each method call on a Workplane implicitly modifies an internal stack of geometric features and local coordinate systems2. While this fluent chaining produces compact code for linear construction sequences, it introduces architectural trade-offs:

* The model state is implicitly hidden inside the Workplane call stack, complicating step-by-step visual inspection and variable debugging2.  
* Standard Python control flow constructs, such as for loops, conditional branching, or direct object filtering, cannot be natively interspersed within a single fluent chain, requiring developers to repeatedly exit and re-enter workplane contexts2.  
* Dynamic method injection and string-parsed selectors limit static type checking (mypy), autocompletion in IDEs, and static analysis1.

Python  
\# build123d Builder Mode Pattern  
from build123d import \*

with BuildPart() as model:  
    Box(100.0, 100.0, 20.0)  
    with BuildSketch(model.faces().sort\_by(Axis.Z)\[-1\]):  
        Circle(5.0)  
    extrude(mode=Mode.SUBTRACT, amount=20.0)

export\_step(model.part, "output\_build123d.step")

build123d was designed as a direct evolution and refactoring of CadQuery, explicitly created to eliminate fluent method chaining while retaining full B-Rep precision over OCCT via OCP1. build123d replaces monkey-patched classes and dynamic namespaces with a PEP 8-compliant, fully type-annotated codebase1. It introduces a Dual API Architecture that caters to different design paradigms1:

> 1. **Builder Mode (Stateful Context Managers):** Builder Mode uses standard Python context managers (with statements)—such as BuildPart(), BuildSketch(), and BuildLine()—to establish an explicit, structured design history1. Within a context block, geometric operations automatically combine pending shapes into the active target body1. This structural pattern allows standard Python control flow (for loops, conditional statements, variable assignments) to execute natively inside construction contexts2.  
> 2. **Algebra Mode (Stateless Functional Paradigm):** Algebra Mode operates as a pure functional interaction layer1. Geometries are explicitly assigned to standard variables and mutated using overloaded mathematical operators (+=, \-=, \*)1. Data flow remains fully transparent, making it ideal for pure functional scripting1.

Additionally, build123d overloads mathematical operators to provide intuitive spatial positioning syntax1. The @ operator extracts exact positions along curves or planes (e.g., line @ 0.5 retrieves the midpoint of a curve), while the % operator evaluates tangent direction vectors along curves at specified parameters1.

| Architectural Dimension | FreeCAD | CadQuery | build123d |
| :---- | :---- | :---- | :---- |
| **Primary Design Paradigm** | Application Document Graph & Feature Trees4 | Fluent Method-Chaining API2 | Dual API: Builder Contexts & Stateless Algebra1 |
| **Underlying Kernel Layer** | Native C++ Open CASCADE (OCCT)4 | OCCT via Python OCP bindings4 | OCCT via Python OCP bindings1 |
| **State Tracking Mechanism** | Document Object Graph (doc.recompute())11 | Implicit Workplane object stack2 | Context managers or explicit variable tracking1 |
| **IDE & Typing Integration** | Requires environment configuration11 | Limited by dynamic method injection1 | Full static typing (PEP 8, mypy, Pylance)1 |
| **Control Flow Support** | Standard Python within macro scripts13 | Constrained within fluent chains2 | Native Python logic within context blocks2 |
| **Selection Syntax** | Sub-element strings (Pad.Faces\[5\])13 | String DSL ("\>Z", \`" | X", "\#Z"\`)2 |
| **Extensibility Model** | C++ workbench plugins or FeaturePython \[cite: 13, 19\] | Class monkey-patching2 | Class subclassing (BaseSketchObject)1 |
| **Manufacturing Export Formats** | STEP, IGES, STL, OBJ, DXF, SVG, FCStd2 | STEP, STL, AMF, 3MF, SVG2 | STEP, STL, SVG, 3MF1 |

## **3\. Topological Handling, Selectors, and the Topological Naming Problem**

A major challenge in parametric Boundary Representation CAD engines is the **Topological Naming Problem (TNP)**16. TNP occurs when upstream modifications to geometric parameters cause the underlying kernel to re-index internal sub-topologies (vertices, edges, faces)16. Because internal geometric entity identifiers generated by OCCT are non-deterministic across recomputations, downstream operations referencing an entity by a fixed index (such as "Face 4") can fail, target the wrong geometric feature, or introduce silent topological errors16.  
Each framework addresses sub-geometry identification and TNP mitigation through distinct mechanisms:  
FreeCAD historically relied on string-indexed sub-element references (e.g., Pad.Faces\[5\]). In early versions, changing an upstream sketch dimension frequently broken downstream references13. Modern iterations of FreeCAD have introduced topology tracking algorithms into the C++ document execution engine, attempting to track and re-bind sub-elements dynamically as shapes evolve. However, when writing raw Python scripts, developers must manually query spatial coordinates or face properties to ensure topological robustness13.  
CadQuery addresses topological referencing by implementing a custom **String-Based Selector DSL**2. Rather than referencing static entity indices, developers query entities relative to global or local coordinate systems using string expressions2:

* faces("\>Z"): Selects the face with the maximum center-point coordinate along the positive Z-axis2.  
* edges("|Z"): Selects all edges parallel to the Z-axis.  
* faces("\<X"): Selects the face positioned furthest in the negative X direction.

While CadQuery's selector DSL avoids hardcoded topological indices—mitigating basic instances of the Topological Naming Problem—it creates new developer experience challenges16. Selector strings are evaluated dynamically at runtime, meaning syntax errors, typos, or invalid expressions cannot be caught by static code analysis prior to script execution1. Additionally, when complex operations alter local geometry, string selector queries can become ambiguous or return empty selection sets, leading to runtime failures4.  
build123d re-architects topology selection by replacing dynamic string parsing with strongly-typed Python collection classes (ShapeList) and standard object filtering methods1. Sub-topologies (Edges, Wires, Faces, Solids) are returned as native Python lists extended with spatial filtering and sorting methods1.

Python  
\# build123d Strongly-Typed Topological Selection  
\# Select top-most face along Z axis  
top\_face \= model.faces().sort\_by(Axis.Z)\[-1\]

\# Filter edges parallel to Z axis  
vertical\_edges \= model.edges().filter\_by(Axis.Z)

\# Sort faces by surface area using custom lambda logic  
largest\_face \= model.faces().sort\_by(lambda f: f.area)\[-1\]

By utilizing standard Python list manipulations, type annotations, and explicit axis constants (Axis.Z), build123d provides static verification1. Typos or invalid property queries trigger immediate IDE errors or type-check failures before runtime execution, preventing latent errors deep within the OCCT kernel execution loop1.

## **4\. Empirical Computational Performance and Developer Experience**

Programmatic CAD workflows require high computational execution speed, rapid iteration cycles, and maintainable developer tooling1.  
Because both CadQuery and build123d wrap the underlying C++ Open CASCADE kernel via Python OCP bindings, execution timing for core geometric calculations (such as boolean operations, extrusions, fillets, and chamfers) is closely aligned4. Benchmarks measuring standard geometric operations yield consistent timing profiles across backends12:

| CAD Framework / Backend Layer | Average Operation Execution Time | Primary Performance Driver & Code Overhead |
| :---- | :---- | :---- |
| **Raw OCP (OpenCASCADE Python)** | **0.0040s** \[cite: 12\] | Direct C++ binding layer; fastest execution but requires 10–20x more code12. |
| **CadQuery** | **0.0096s** \[cite: 12\] | Low abstraction overhead over OCP; minimal runtime wrapper impact2. |
| **build123d** | **0.0120s** \[cite: 12\] | Low abstraction overhead; slight delta due to class validation and typing checks1. |

Raw OCP provides the fastest raw execution speed (\~0.0040s per operation) because it eliminates intermediate abstraction wrappers12. However, scripting directly in raw OCP requires verbose, low-level memory and topological management, making it impractical for general mechanical design12. CadQuery (\~0.0096s) and build123d (\~0.0120s) add minor wrapper overhead to deliver high-level geometric primitives12. The minimal timing difference between CadQuery and build123d is negligible in interactive design workflows12.  
Developer experience diverges significantly across the three engines:  
IDE integration and static analysis vary based on API design choices1. build123d provides full autocomplete and static type checking in modern IDEs (such as VS Code and PyCharm) because its source code avoids dynamic class monkey-patching1. Static analysis tools like Pylance and mypy can catch type mismatches and invalid method signatures prior to execution1. In contrast, CadQuery's dynamic method injection limits static autocomplete capabilities2. FreeCAD requires configuring path wrappers (sys.path.append) to run inside external IDEs11.  
Both CadQuery and build123d integrate with reactive Python notebook environments (e.g., Marimo, Jupyter) via HTML display wrappers (\_repr\_html\_()), enabling interactive, inline WebGL 3D model visualization via rendering backends like ocp\_vscode2.  
Source-code-based CAD allows integration with automated software testing frameworks2. Engineering teams can configure continuous integration pipelines to execute build123d or CadQuery scripts headlessly, outputting STEP or STL artifacts while running geometric validation checks (e.g., verifying manifold properties, watertight surfaces, total mass properties, and clearance tolerances)1.

## **5\. Agentic CAD: Large Language Models and Autonomous Design Loops**

The emergence of Large Language Models (LLMs) has enabled **Agentic CAD**, where autonomous AI agents generate, inspect, and refine 3D mechanical models from natural language prompts, engineering drawings, or freehand sketches6.  
In an agentic workflow, an AI core accepts multimodal user inputs and engages in iterative code synthesis7. The agent generates CAD scripts, executes them headlessly, receives symbolic error logs or rendered visual feedback, and automatically repairs mistakes7.  
Code-CAD serves as an ideal framework for generative AI relative to raw 3D mesh synthesis or point cloud estimation:

* Pre-trained LLMs excel at writing structured code and reasoning about formal programming syntax4.  
* Generating source code that compiles via B-Rep kernels produces exact, analytic geometry, avoiding boundary defects, non-manifold edges, and mesh approximations1.  
* Code execution is deterministic; when an execution fails, compiler errors, execution tracebacks, or geometric gate failures provide explicit feedback that AI agents can use to fix bugs iteratively7.

Evaluating AI code synthesis across different CAD engines highlights significant variations in error rates, common failure modes, and code repair efficiency4. Benchmark studies evaluating models like gemini-1.5-flash on standardized mechanical part generation suites show distinct performance profiles across target CAD engines4:

| Metric / Dimension | OpenSCAD (Declarative CSG) | CadQuery (Fluent B-Rep) | build123d (Pythonic B-Rep) |
| :---- | :---- | :---- | :---- |
| **Code Errors per Generation** | **\~0.4 errors / model** \[cite: 4\] | **High Error Rate** \[cite: 4\] | **\~1.4 to 1.7 errors / model** \[cite: 4\] |
| **Primary AI Failure Modes** | Minor syntax slips; easily repaired in one pass4. | Hallucinated method names; argument order errors; broken export pipelines4. | Calling non-existent methods; type class confusion (e.g., Wire vs Edge)4. |
| **Impact of API Prompting** | Minimal improvement (small API surface area)4. | Limited improvement (fluent stack confuses context)4. | High improvement (errors dropped from 40 to 33 when provided with API signatures)4. |
| **Kernel Output Quality** | Approximated mesh; lacks exact B-Rep and proper fillets4. | Exact B-Rep STEP files4. | Exact B-Rep STEP files1. |

OpenSCAD achieves the lowest code error rate (\~0.4 errors per generation) due to its small, declarative language surface area4. However, because OpenSCAD outputs tessellated meshes rather than true B-Rep STEP files, it cannot produce exact curved surfaces, analytic fillets, or chamfers required for downstream mechanical engineering1.  
CadQuery exhibits high error rates during LLM code synthesis4. Language models frequently hallucinate non-existent fluent method names, scramble argument ordering, or invoke invalid methods on the implicit Workplane stack4. Additionally, CadQuery's native exporters often drop color or part metadata, causing multi-part export pipeline failures unless custom exporters are implemented4.  
build123d experiences a higher code error rate than OpenSCAD (\~1.4 to 1.7 errors per generation) due to the vast surface area of the B-Rep API4. However, because build123d relies on explicit, standard Python type signatures, providing the LLM with an introspected API reference sheet directly reduces code errors (dropping total errors from 40 down to 33 across test runs)4.  
To overcome the hallucination rates of LLMs writing unconstrained B-Rep code, researchers developed specialized paradigms, notably the **Explicit Context Imperative Paradigm (ECIP)** implemented in platforms like **CADDesigner** and SimpleCADAPI6.  
ECIP restructures CAD operations to minimize context ambiguity during LLM generation6:

> 1. **Explicit Type Annotations in Function Signatures:** To prevent LLMs from confusing geometrically adjacent entities (such as a 1D Edge vs a closed 1D Wire vs a 2D Face), ECIP enforces explicit return-type function naming, using patterns like ActionName\_rReturnType (e.g., extrude\_rSolid(), get\_edges\_rEdgeList())9. The function signature itself declares return-type semantics directly within the LLM context window9.  
> 2. **Structured Error Triples for Automated Repair:** Standard runtime tracebacks offer poor guidance for LLM self-healing9. ECIP intercepts runtime failures and formats them into structured error triples:  
>    ![][image1]  
>    Here, ![][image2] identifies the root geometric cause (e.g., "Intersecting Fillet Radius"), ![][image3] pinpoints the exact line and entity ID, and ![][image4] provides actionable repair instructions (e.g., "Reduce fillet radius below 2.0mm")9.  
> 3. **Self-Evolving Composite Operations:** When an agent successfully validates a complex feature (such as a custom mounting flange or thread profile), the script sequence is encapsulated as a high-level composite operation and saved to a reusable knowledge base7. Subsequent agent runs can invoke this composite operation directly, expanding the language vocabulary and increasing one-shot generation success rates9.

In benchmark evaluations like **CADGenBench** (comprising 81 mechanical part tasks split across 49 generation fixtures and 32 editing fixtures), ECIP-driven agents consistently demonstrate tighter error distributions, fewer repair iterations, and higher final CAD quality scores6. CADGenBench uses a hard validity gate—verifying watertight manifolds, non-manifold edge defects, and exact STEP file export validity—followed by a weighted evaluation score combining 40% shape similarity (volumetric IoU and Hausdorff distance) with 60% interface and topological feature matching10.

## **6\. Strategic Conclusions and Recommendations**

Synthesizing architectural features, empirical execution metrics, and AI synthesis benchmarks yields clear recommendations across primary deployment domains:

| Primary Deployment Domain | Recommended Framework | Strategic Justification |
| :---- | :---- | :---- |
| **Interactive Desktop Modeling & GUI Tooling** | **FreeCAD** | Unmatched choice when an interactive graphical interface is required alongside macro scripting11. Provides a broad workbench ecosystem, C++ core stability, and desktop UI workflows11. |
| **Human Programmatic Code-CAD & Hardware Design** | **build123d** | Superior choice for human engineers authoring version-controlled CAD repositories1. Dual API, context managers, static type safety, and Pythonic list filtering remove the opacity of fluent method chains1. |
| **Legacy Code-CAD Infrastructure** | **CadQuery** | Pioneer of Pythonic B-Rep modeling with broad community adoption4. However, its fluent chaining and dynamic string selectors make it less maintainable for new codebases compared to build123d1. |
| **Autonomous AI Agent Code Synthesis (Agentic CAD)** | **ECIP / SimpleCADAPI (over OCP)** | Optimal choice for agentic CAD automation6. Explicit return-type function signatures, self-describing APIs, structured error triples (ErrCau, ErrLoc, CorrAct), and composite operation libraries significantly reduce LLM hallucination rates and execution costs8. |

The programmatic CAD ecosystem is transitioning toward tight integration with modern software engineering and autonomous AI agents1. For human developers, **build123d** represents a major advance over legacy tools, offering a clean, type-safe, dual-API Python interface over Open CASCADE1. For autonomous AI workflows, raw CAD APIs benefit from being wrapped in explicit, context-aware frameworks like **ECIP**6. Combining self-describing function signatures, structured execution feedback, and automated geometric validation gates enables autonomous agents to convert complex engineering prompts into verified, manufacturing-ready 3D CAD models7.

#### **Obras citadas**

> 1. gumyr/build123d: Pythonic CAD for Parametric 3D Modeling, [https\://www\.blog.brightcoding.dev/2026/09/21/gumyrbuild123d-pythonic-cad-for-parametric-3d-modeling](https://www.blog.brightcoding.dev/2026/09/21/gumyrbuild123d-pythonic-cad-for-parametric-3d-modeling)  
> 2. Build123d: Python CAD Framework Guide | PDF \- Scribd, [https\://www\.scribd.com/document/887005684/Build123d-Readthedocs-Io-en-Latest](https://www.scribd.com/document/887005684/Build123d-Readthedocs-Io-en-Latest)  
> 3. Not trying to be a negative nancy, but whats the point of using code, [https\://www\.reddit.com/r/openscad/comments/1o6lnyv/not\_trying\_to\_be\_a\_negative\_nancy\_but\_whats\_the/](https://www.reddit.com/r/openscad/comments/1o6lnyv/not_trying_to_be_a_negative_nancy_but_whats_the/)  
> 4. OpenSCAD vs CadQuery vs Build123d: which CAD engine can AI, [https\://grandpacad.com/en/blog/openscad-vs-cadquery-vs-build123d](https://grandpacad.com/en/blog/openscad-vs-cadquery-vs-build123d)  
> 5. Or better, Build123D, which is CadQuery-compatible but does not, [https\://news.ycombinator.com/item?id=40563489](https://news.ycombinator.com/item?id=40563489)  
> 6. CADDesigner: Conceptual CAD Model Generation with a General, [https\://www\.alphaxiv.org/abs/2508.01031](https://www.alphaxiv.org/abs/2508.01031)  
> 7. (PDF) CADDesigner: Conceptual Design of CAD Models Based on, [https\://www\.researchgate.net/publication/394294015\_CADDesigner\_Conceptual\_Design\_of\_CAD\_Models\_Based\_on\_General-Purpose\_Agent](https://www.researchgate.net/publication/394294015_CADDesigner_Conceptual_Design_of_CAD_Models_Based_on_General-Purpose_Agent)  
> 8. 562590763/CADDesigner-Code \- GitHub, [https\://github.com/562590763/CADDesigner-Code](https://github.com/562590763/CADDesigner-Code)  
> 9. CADDesigner: Conceptual Design of CAD Models Based on ... \- arXiv, [https\://arxiv.org/html/2508.01031v4](https://arxiv.org/html/2508.01031v4)  
> 10. build123d-mcp/CHANGELOG.md at main \- GitHub, [https\://github.com/pzfreo/build123d-mcp/blob/main/CHANGELOG.md](https://github.com/pzfreo/build123d-mcp/blob/main/CHANGELOG.md)  
> 11. Headless FreeCAD, [https\://wiki.freecad.org/Headless\_FreeCAD](https://wiki.freecad.org/Headless_FreeCAD)  
> 12. tkoyama010/marimocad \- GitHub, [https\://github.com/tkoyama010/marimocad](https://github.com/tkoyama010/marimocad)  
> 13. freecad-scripts \- Akilima, [https\://www\.akilima.tech/skills/github/awesome-copilot/freecad-scripts](https://www.akilima.tech/skills/github/awesome-copilot/freecad-scripts)  
> 14. How To Script A Sketch In FreeCAD Using Python \-, [https\://singerlinks.com/2023/11/how-to-script-a-sketch-in-freecad-using-python/](https://singerlinks.com/2023/11/how-to-script-a-sketch-in-freecad-using-python/)  
> 15. Help in using Freecad in headless mode \- Reddit, [https\://www\.reddit.com/r/FreeCAD/comments/175auz8/help\_in\_using\_freecad\_in\_headless\_mode/](https://www.reddit.com/r/FreeCAD/comments/175auz8/help_in_using_freecad_in_headless_mode/)  
> 16. CAD as Code \- OPUS, [https\://opus4.kobv.de/opus4-hm/files/1358/Straub\_2026.pdf](https://opus4.kobv.de/opus4-hm/files/1358/Straub_2026.pdf)  
> 17. Direct API Reference \- Build123d \- Read the Docs, [https\://build123d.readthedocs.io/en/latest/direct\_api\_reference.html](https://build123d.readthedocs.io/en/latest/direct_api_reference.html)  
> 18. build123d \- PyPI, [https\://pypi.org/project/build123d/](https://pypi.org/project/build123d/)  
> 19. Freecad Scripts (Grade A) \- Claude Skill \- Skills Directory, [https\://www\.skillsdirectory.com/skills/paulasilvatech-freecad-scripts-8ae8247f](https://www.skillsdirectory.com/skills/paulasilvatech-freecad-scripts-8ae8247f)  
> 20. CADing and 3D printing like a software engineer | Hacker News, [https\://news.ycombinator.com/item?id=42398651](https://news.ycombinator.com/item?id=42398651)  
> 21. AI-driven parametric 3D model generator \- Dominik Szaradowski, [https\://szaradowski.com/blog/building-ai-driven-parametric-3d-model-generator](https://szaradowski.com/blog/building-ai-driven-parametric-3d-model-generator)  
> 22. Conceptual Design of CAD Models Based on General-Purpose Agent, [https\://arxiv.org/html/2508.01031v1](https://arxiv.org/html/2508.01031v1)  
> 23. CADDesigner \- GitHub Pages, [https\://562590763.github.io/CADDesigner/files/CADDesigner.pdf](https://562590763.github.io/CADDesigner/files/CADDesigner.pdf)  
> 24. CAD-LLM: Large Language Model for CAD Generation, [https\://www\.semanticscholar.org/paper/CAD-LLM%3A-Large-Language-Model-for-CAD-Generation-Wu-Khasahmadi/f9421be64839fd008f87acc12da22719a7112310](https://www.semanticscholar.org/paper/CAD-LLM%3A-Large-Language-Model-for-CAD-Generation-Wu-Khasahmadi/f9421be64839fd008f87acc12da22719a7112310)  
> 25. HuggingAI4Engineering/cadgenbench-data · Datasets at Hugging, [https\://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data](https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data)

[image1]: <data:image/png;base64,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>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEMAAAAZCAYAAABq35PiAAAEAElEQVR4XtVXTYhPURQ/t6HIMMOIRmomZcFGmWbHgpQsphRFka0VxcbHSsrCThobIqN8L6zsJxRloSmiUKwUYWOU5OP83jn3vXvvu+++9/+/fxq//Ob93/m6555737kXEcPgTwhPGLVIwLHv1DVwSL21hUSriVmjzlCyMSVJM3Ti1oltBdw0k+FabQaqcUkqFWrTxDSKzLGZd9wqLi0jt0s5hLrwPYlB5jC7uE598vDjNImatqnW7mBezGlM8Vtp5HmYOS/36g2WMM8yvzL/ML8w3zF3kWS8P7cM8q+eTjusY+5m/iJJ6IK+W55hvmFOM/vFpYxYcvE24gk/MWeZR1i+SGWrmfeYd0mKFCAatGMgSirSe5JiTIQKxROSwjVCaiDGKPMtc48v9rzGSApVj+RgMaWVmbia4sXA5G0BrjDHc01daasBr3MkYw0kYixg3g+FPqqdm6HK3wTFELtjwuzlNHObGOcYImly85nLAx3krk6bIa1kviQZKwke9WQoI2mywyRjx2YDGXQopot+1gxGPSIIdwYmcYekIIIiEGSwhc8hkqaH73/E0YGu7jkJNnGgn6r3UU602IlZPgbN9irzMkkfAws3QwepGNvOA8WzsilrGoUzvi0GBkTjvETSVIti+EAzhf0+kjA7mbYJQjcd6E6oDkna5DoBGusMR8PTAvGPOu8AJuwWA5jQgkxFCh5FuDPgNkkohgTo46f9LKDFhL8zxyOnRlYM46+sBY7yboqhkMGMfAYHqLxz48UIdwZsy3kDBv/CYkCmPSMDVuQGc7G+Y8Lo9uj6IezOUF0+AWAN8wPlxfB04W8UzpXh031EWARDr/n5gMo7t1kxalAuhg/+1ukap2UvXloMUyqGKRXDA/xvGhlLmlxFJUg+g0xoZDGeMo8zF6oeueIe5KJdMXR8LoYJg5CT3SnSgfXG3MHOKGEzR/nBzzF/7h5QNJs8zCY1P/fCjlxhs4y5UWVeMfQTlmKYBsVQfCQJgtWwxyA/DY6xvczf5B+tQ0Z6xhYK11OOtscVOivClsd4z5jrA+V2ks9hxJFNkjRBGw+6GeZ1kuv7eZXbE0V3VYYXKnvIXOXIS3CPwhQ/k72ASS9RebZaoN1RsXhVn95Wwp1DJvmKcGSarDgoxKhrSLL6OOqnmbdJJraBJD7uLWu1SgPMW8xvagfiNJs1RT5zFpgDr5ZBwXCkj6qsCuGFayn/ynay/x9e6jfF5Qy7EJe9FSSnYhp1+m4QOXJzJFQBypZlSVN079kAvQreqzhzEMXUupxklVuVPFNUKKPi1hkq2nn/G/Q+xzBi+N4AXbg0cqoxab7udfo2cGOnumcdWrj+lwjn672HSheBLmXaCBoAj7+hcsL7yZpzdgAAAABJRU5ErkJggg==>

[image3]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAD4AAAAZCAYAAABpaJ3KAAADqUlEQVR4XsVYT4hPURQ+L9QIDRFNqdmpKTtRyoKykZSFBdkoGxvsTFlNSqJshIW/sZAksZMFv6KoWSv5U6ZkQZIFhcz4vnfufe++++69v/t7M8NXn/veOd8555777ru/N0TmC4VvyEEqKOUzKDI0s0W8hPHEBWmfhwGkedocUY6mCwbKW4oTEQlXH2cemKJ7mq6RdZybIZQtZAvB1e0AL2XwCLjQxHRDIavx70WMfm7yVC2Lw/f5901EvMY8Bu7B9R+MMxgv8L5mcRLjW7AHLtWQzlgsutB7wUfgR/CQaK2ttcxDZP4DI5JnCpwBd/kOgxfCRaqCm1mCd4Vrb1UdF28xXUWkTB8MJK4whTjTeJWAu4EkroEbrcMiWSrpbDfeQDo2gFBAyNbGlOhWN0+8DOLkSOIEuN1cEwvAlRyhXIRxlZobxYYKNVA3ZP1GkW68gSrnCLhGtF4MrEXdCCqZmorYMpSNw2u3OpPfkbpxF+OFvhaMOQy+Bz+Do+AG8Luo/yg4Ce0bjL80tEJG49VU14L3wKvgA/ALeEB08V2sAz+BNxB7V7RmeSDHmia0cZHToofNZah54IUaJ3qi+v2ieXeDSxw/41ichTmhM46PyGi8Wvyv4Hpn+ry4Dv409zw0H4K/rUC05itwONU0YRu3T5z681JOsAyttrbx9sAfEnjvSxRlY8d9s4NU46yxAjnGCn26L6V6lSownvMl7C57V7t9xNv3G6fUfce53W6By8x9T7QYi4bgxjZgppBqnDnvi86Fc+pJW6eNF6Xd1dWI96ow/lbjHrZAeFPqj5iexBrXhJHGq9m0Gnc2Mg/RK1I39FTqBbewTzzeuPTvnfAaL/yoCZAfMxY9iTWucBoPlm817oB1zkm9hTk3ntQu3K2+WfR9/1C7wwjNhKehPazsacmRBfmlNa1Pogp9DvId3ybtfIybEP0JDP300HZWNAdfIWs7CH6Teh7EKNK/xni7vNPnsVO09jGn9CbRQ9B+d9BD27C99zEu+uHSjzxkbFK72i7tK+L+nFneMD4uot1Z/GaoNPV1YW38tDUouDg83Z8JP5+L8lN3n/g/Z0V5kk+Dk+Bj3D9pPBL/8cw9ulXg/yP0ieTC8Q+dsuGIdrmojqODiPpf4n9PYcD6cXnlaV/ko0NIGAMkGkA6S9hKoYohWxqxiJi9jVKZL4/CSzEHGTOQqhLxRcyS8mQjmSLpnGP0q9XPn0SH4EZILD5m74rsfNnCfARSBkzzjr/ltrZTUB2mQQAAAABJRU5ErkJggg==>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEoAAAAZCAYAAACWyrgoAAAEWklEQVR4XtVXTchWRRQ+gwlGXyQZxgfClwlCaRszQ3BThHwhQotcSJsIwpVLlUTQTYtaioSIKe6kol0kLewlg/6gNoUhCBVfuhANAgUt0ue5Z2beM3Pn/uUVPx943nvnnDNnzpw5M/NeEQuXtBYFbEh3E15b35KuJBsdtUFqgvHRNERZPpWGt7LdIgGDGyfA3EveztGlt+i2XQnOgjNR4mQZfslRYUPpDkteBJ+bNnv0uEd4FvwMvA1eAv8GvwCXgh+B2+9jbE+Av4BnwIdTVSmokqwJ/W2ZiAOiCfoZXOLl9PAy+DFeqdvu5VFbGqIkGwGvgP+B1zHC87lyKFzYLTHYflEzgIvgU5k8YJ8wic4mqp/jLrR7SbRfgz+JLubEKopod0wczgVd4HnEwQ+1OH8GvCqFimrpMxLiADwCdkisqgzD4lgB/pgLpyg7m4f4Hzy35Apj/yh4zuWJUrCEefBz+5ZA+eOiF8EK+LRRcIsz6CVOz6Cw5TM49mFVPwmeF13YLrDPLH4Zm72EOMY70s9HgnfBy+DTucKAg74lWlkBq8BPwQvgh6IV96YwEE3FKdFgyK/A30Qr4XPwMdEzj/wd3I0+V0TPx7mqd4WYUyYonEvcMuw3vZHrWAt+LxrDJ+AtcL/oOXdNpnEF0q4TNGKwzHxf8Ab8C1xvZJzVSfAmuNnL6JO+WbGsrLc9+c6JTkQDfQN8TXSlH2FHA/o9Ytrh9mOF5eBtyFvR7hAm7VepFigmXs/cgTgOBwuiFWKgTqPrFKweBsugLUIA4aCsEgUfCwU/M04TdQN8IdNZsJq+yWT0/61oZVYC759Vx6rlxcR+TWhOlHdUmj1Xswq2MBmLY+Cr/p2DTFy9/EMAE98OFUVG+HFCRXFibdf9PPgv+Icht4+t3OCTZ2gYP4/NopCojtkDq0U77coVBg+BJ2S68rQ/J3rIK3Sc5kTV4nA9ElUd4tx2B0V9Ba6Tahx3KPPbkKja4DZR8UKZqhV5L7YLzhMwmTyEw1bj5JiA/FzTAFy69Txz9EhU3HZen4TOmP32i2CFsdIWYJoeJaErny5JFGOc+Gc0aAKd/iDa+YPMjAfkUUmv/znR2+60xMPXbRPdwnvZ8HZr8PonnmSEV3IVmYQbaL+U6NRgg+gfTI7DTytjUIE+uSjvS3r9bxK9aHhOWRkvoICQUO4UXkin4Dj9ji3mSoVMyB7RyZ4VPZPgoPrmy76tKqxCPw4Oe8d/zbTbKeW/B2ReOVZH2v9oXN1MHyO3t6XljLHhTcdF4OKfxQ7+UtLLailM3/M234Ebja4ZWfJYOa97bhVf2vUEJ4HPusIeXwRgwpfnQgPqTSXVZ1lHp02nwSCM6m1UZ4sE1ZwGTGyA6XD0dh4N0w+wRvQyMjD2Q7t2oeyvLH1A0W8y/azGxuijju4wgxs4RM8dcT9RBdgWZWnOuSBvW5R0Jdn/hnHW7bfbIiC19K1C9yDKVXm7Gy0TaVGNiTtwXc5mXJh+HgAAAABJRU5ErkJggg==>