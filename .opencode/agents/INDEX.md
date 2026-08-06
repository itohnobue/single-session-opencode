# Agent Directory (109 agents)

Quick reference for agent selection. Pick the MOST specialized agent for the task — domain-specific checklists and anti-patterns only work when the agent matches the domain.

## Mode Tags (Recommendation — Not a Strict Rule)

Each agent has a **Mode** tag based on real-project A/B/C testing on real codebases. The tag indicates which cognitive approach the agent is best at. **This is a recommendation to consider alongside specialization, not a restriction** — any agent can do any task, but matching the mode to the task type produces measurably better results.

**How to use:** First, pick the most specialized agent for the domain (e.g., `postgres-pro` for PostgreSQL). Then consider whether the mode fits the task:

- **TRACE** — excels at following data/logic/flow through code. Prefer for: bug hunting, cross-file dependency tracing, pipeline analysis, architecture assessment, performance profiling. The agent traces call chains, follows data transformations, and maps how components connect.
- **SWEEP** — excels at checking many items systematically against a checklist. Prefer for: security audits, idiom/style reviews, configuration sweeps, comprehensive code reviews, test coverage assessment. The agent methodically checks each pattern, method, or config item.
- **KNOW** — excels at applying deep domain/framework expertise. Prefer for: framework-specific work (.NET, Spring, Django), API/gotcha-heavy reviews, tasks where knowing specific library behaviors matters more than tracing or sweeping.

When in doubt, specialization always wins over mode. A `TRACE` security-reviewer is still better at security than a `KNOW` react-pro for a security task.
## Language Implementation

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| python-pro.md | Python expert | TRACE | Python development, refactoring, optimization |
| python-reviewer.md | Python reviewer | SWEEP | Python code review (PEP 8, idioms, security) |
| golang-pro.md | Go expert | TRACE | Go architecture, concurrency, performance |
| go-reviewer.md | Go reviewer | SWEEP | Go code review (idiomatic Go, error handling) |
| go-build-resolver.md | Go build fixer | SWEEP | Go build/vet errors, linter warnings |
| typescript-pro.md | TypeScript expert | TRACE | TS architecture, type-level programming, refactoring |
| javascript-pro.md | JavaScript expert | SWEEP | JS optimization, async patterns, Node.js |
| rust-pro.md | Rust expert | SWEEP | Rust development, async patterns, systems programming |
| java-pro.md | Java expert | KNOW | Java 21+, Spring Boot 3.x, virtual threads |
| csharp-pro.md | C# expert | KNOW | Modern C#, .NET, records, pattern matching |
| cpp-pro.md | C++ expert | TRACE | Modern C++, RAII, templates, performance |
| c-pro.md | C expert | SWEEP | Systems programming, embedded, kernel modules |
| kotlin-pro.md | Kotlin expert | KNOW | Android/Jetpack Compose, KMM, coroutines |
| swift-pro.md | Swift/iOS expert | SWEEP | SwiftUI, UIKit, async/await concurrency |
| haskell-pro.md | Haskell expert | KNOW | Advanced type systems, pure functional design |
| elixir-pro.md | Elixir expert | TRACE | OTP, supervision trees, Phoenix LiveView |
| ruby-pro.md | Ruby expert | TRACE | Metaprogramming, Rails patterns, optimization |
| php-pro.md | PHP expert | SWEEP | Generators, iterators, modern OOP |
| scala-pro.md | Scala expert | SWEEP | Functional programming, Spark, ZIO/Cats |
| julia-pro.md | Julia expert | TRACE | Scientific computing, multiple dispatch, optimization |
| bash-pro.md | Bash expert | KNOW | Defensive scripting, CI/CD, automation |
| posix-shell-pro.md | POSIX shell expert | SWEEP | Portable sh scripts across Unix systems |

## Web Frameworks

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| react-pro.md | React expert | SWEEP | React components, hooks, state management |
| nextjs-pro.md | Next.js expert | KNOW | SSR/SSG, App Router, SEO optimization |
| vue-pro.md | Vue expert | TRACE | Vue 3 Composition API, Nuxt.js |
| django-pro.md | Django expert | KNOW | Django 5.x, async views, DRF, Celery |
| fastapi-pro.md | FastAPI expert | SWEEP | Async APIs, SQLAlchemy 2.0, Pydantic V2 |
| rails-pro.md | Rails expert | KNOW | Rails 7+, Hotwire, ActiveRecord |
| spring-boot-pro.md | Spring Boot expert | TRACE | WebFlux, microservices, reactive |
| flutter-pro.md | Flutter expert | TRACE | Dart 3, multi-platform, state management |
| electron-pro.md | Electron expert | TRACE | Desktop apps, IPC, native integration |
| wordpress-master.md | WordPress architect | KNOW | Themes, plugins, multisite, enterprise |

## Architecture & Design

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| backend-architect.md | Backend architect | KNOW | API architecture, database schema design |
| api-designer.md | API designer | SWEEP | REST/GraphQL API design, standards |
| graphql-architect.md | GraphQL architect | SWEEP | Schema architecture, federation, subscriptions |
| database-architect.md | Database architect | TRACE | Technology selection, schema modeling |
| microservices-architect.md | Microservices architect | SWEEP | Service decomposition, CQRS, resilience |
| event-sourcing-architect.md | Event sourcing architect | TRACE | Event stores, projections, sagas |
| design-system-architect.md | Design system architect | SWEEP | Tokens, component libraries, theming |
| monorepo-architect.md | Monorepo architect | KNOW | Nx, Turborepo, Bazel, dependency management |
| llm-architect.md | RAG architect | KNOW | Vector databases, chunking, retrieval |

## DevOps & Infrastructure

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| devops-engineer.md | DevOps engineer | SWEEP | CI/CD, containerization, automation |
| deployment-engineer.md | Deployment engineer | SWEEP | CI/CD pipelines, GitOps, container orchestration |
| kubernetes-architect.md | K8s architect | SWEEP | EKS/AKS/GKE, service mesh, GitOps |
| cloud-architect.md | Cloud architect | TRACE | AWS/Azure/GCP, Terraform, FinOps |
| terraform-pro.md | Terraform expert | KNOW | IaC, state management, multi-cloud |
| hybrid-cloud-architect.md | Hybrid cloud architect | TRACE | Multi-cloud, edge, cross-cloud automation |
| platform-engineer.md | Platform engineer | TRACE | Internal platforms, self-service infrastructure |
| sre-engineer.md | SRE | SWEEP | SLOs, reliability, chaos testing, toil reduction |
| observability-engineer.md | Observability engineer | KNOW | Monitoring, logging, tracing, SLI/SLO |
| network-engineer.md | Network engineer | KNOW | Cloud networking, zero-trust, SSL/TLS |
| service-mesh-pro.md | Service mesh architect | TRACE | Istio, Linkerd, traffic management |

## Security

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| security-reviewer.md | Security reviewer | KNOW | Vulnerability detection, OWASP Top 10 |
| penetration-tester.md | Penetration tester | TRACE | Security audits, compliance (NIST, SOC2) |
| threat-modeling-pro.md | Threat modeling | KNOW | STRIDE, PASTA, attack trees, risk assessment |
| backend-security-coder.md | Backend security | SWEEP | Auth, input validation, API security |
| frontend-security-coder.md | Frontend security | SWEEP | XSS prevention, sanitization |
| mobile-security-coder.md | Mobile security | SWEEP | Input validation, WebView security |

## Database

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| postgres-pro.md | PostgreSQL expert | KNOW | Indexing, query optimization, JSONB |
| sql-pro.md | SQL expert | TRACE | OLTP/OLAP, cloud-native databases |
| database-optimizer.md | Database optimizer | SWEEP | Query bottlenecks, schema refinement |
| database-reviewer.md | Database reviewer | SWEEP | PostgreSQL review, Supabase |
| vector-database-engineer.md | Vector DB engineer | TRACE | Pinecone, Weaviate, Qdrant, pgvector |

## Testing & Quality

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| code-reviewer.md | Code reviewer | TRACE | Code review for quality, security, maintainability |
| tdd-guide.md | TDD specialist | TRACE | Write-tests-first methodology, 80%+ coverage |
| test-automator.md | Test automation | TRACE | Test strategy, CI/CD testing pipelines |
| e2e-runner.md | E2E testing | TRACE | Playwright, browser testing, flaky test management |
| qa-pro.md | QA expert | TRACE | Test plans, quality processes |

## AI & ML

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| ai-engineer.md | AI engineer | SWEEP | LLM apps, RAG, prompt pipelines |
| ml-engineer.md | ML engineer | TRACE | Model deployment, monitoring, ML lifecycle |
| mlops-engineer.md | MLOps engineer | KNOW | ML pipelines, experiment tracking, Kubeflow |
| prompt-engineer.md | Prompt engineer | TRACE | LLM interaction design, agentic workflows |
| mcp-developer.md | MCP developer | TRACE | Model Context Protocol servers/clients |

## Frontend & Mobile

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| frontend-developer.md | Frontend engineer | TRACE | React components, accessibility |
| ui-designer.md | UI designer | SWEEP | Interface design, prototyping |
| ux-designer.md | UX designer | SWEEP | Usability, accessibility, user research |
| ios-pro.md | iOS developer | TRACE | Swift/SwiftUI, iOS 18, App Store |
| mobile-developer.md | Mobile developer | SWEEP | React Native, Flutter, cross-platform |

## Documentation

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| documentation-pro.md | Tech writer | TRACE | API references, guides, troubleshooting docs |
| technical-writer.md | Technical writer | SWEEP | READMEs, ADRs, documentation automation |
| docs-architect.md | Docs architect | TRACE | Long-form technical manuals from codebases |
| api-documenter.md | API documenter | TRACE | OpenAPI specs, SDK guides, Postman collections |
| doc-updater.md | Doc updater | SWEEP | Codemaps, README updates |
| tutorial-engineer.md | Tutorial creator | KNOW | Step-by-step guides, onboarding content |
| mermaid-pro.md | Diagram specialist | TRACE | Flowcharts, sequences, ERDs, architecture diagrams |

## Incident & Troubleshooting

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| incident-responder.md | Incident commander | KNOW | Production incidents, urgent response |
| devops-incident-responder.md | DevOps incident responder | KNOW | Root cause analysis, monitoring-driven fixes |
| devops-troubleshooter.md | DevOps troubleshooter | KNOW | Log analysis, K8s debugging, distributed tracing |
| debugger.md | Debugger | SWEEP | Errors, test failures, unexpected behavior |

## Specialized

| File | Agent | Mode | Use When |
|------|-------|------|----------|
| build-engineer.md | Build engineer | SWEEP | webpack, Vite, esbuild, build optimization |
| build-error-resolver.md | Build error fixer | TRACE | Build/TypeScript errors, minimal fixes |
| adversarial-reviewer.md | Adversarial reviewer | TRACE | Falsifies findings against source — checks results for errors on high-priority work |
| cli-developer.md | CLI developer | TRACE | Command-line tools, argument parsing |
| data-engineer.md | Data engineer | KNOW | ETL/ELT, Spark, Airflow, Kafka |
| data-scientist.md | Data scientist | SWEEP | Statistical analysis, pandas, scikit-learn |
| data-researcher.md | Data researcher | SWEEP | Data discovery, pattern recognition |
| dependency-manager.md | Dependency manager | TRACE | Package management, security auditing |
| dotnet-core-pro.md | .NET Core expert | KNOW | .NET 8, minimal APIs, cloud-native |
| dotnet-framework-pro.md | .NET Framework expert | KNOW | .NET 4.8, Web Forms, WCF, legacy |
| dx-optimizer.md | DX optimizer | KNOW | Developer experience, tooling, workflows |
| full-stack-developer.md | Full-stack developer | SWEEP | End-to-end features, database to UI |
| legacy-modernizer.md | Legacy modernizer | TRACE | Incremental modernization, monolith decomposition |
| performance-engineer.md | Performance engineer | TRACE | Bottleneck identification, scaling strategy |
| planner.md | Planning specialist | SWEEP | Feature planning, complex refactoring |
| product-manager.md | Product manager | SWEEP | Product vision, strategy, roadmaps |
| refactor-cleaner.md | Refactor/cleanup | KNOW | Dead code removal, consolidation |
| research-analyst.md | Research analyst | KNOW | Structured research, source evaluation |
| websocket-engineer.md | WebSocket engineer | SWEEP | Real-time messaging, Socket.IO |
| web-searcher.md | Web researcher | SWEEP | Internet search + synthesis |
