# ECK Design Specification v0.1

本目錄是ECK的規格單一真實來源（Single Source of Truth）。文件不是用頁數代表完成度，而是以「可實作、可測試、可追溯」為準則。十三卷共同描述Digital Life Kernel v0.1及其長期邊界。

## 狀態標記

- **Implemented**：程式碼已存在，且有自動測試或驗收證據。
- **Experimental**：已有原型，但介面或證據尚不足以凍結。
- **Future**：已定義方向，v0.1沒有實作。
- **Research**：仍是可被證偽的假說，不得視為產品承諾。

## 文件索引

1. [Vision, Philosophy & Constitution](01-vision-philosophy-constitution.md)
2. [Architecture Specification](02-architecture-specification.md)
3. [Digital Life Kernel](03-digital-life-kernel.md)
4. [Memory System](04-memory-system.md)
5. [Experience Engine](05-experience-engine.md)
6. [Brain Runtime](06-brain-runtime.md)
7. [Prediction & World Action Model](07-prediction-world-action-model.md)
8. [Planner, Reflection & Curiosity](08-planner-reflection-curiosity.md)
9. [Contracts & API](09-contracts-api.md)
10. [Runtime & Infrastructure](10-runtime-infrastructure.md)
11. [Development Guide](11-development-guide.md)
12. [Testing & Validation](12-testing-validation.md)
13. [Roadmap & Research](13-roadmap-research.md)
14. [v0.1.0 Completion Report](14-v0.1.0-completion-report.md)
15. [v0.2.0 Development Specification](15-v0.2.0-development-specification.md)
16. [Current Information Critical Learning](16-current-information-critical-learning.md)
17. [Autonomous Learning, Portability & Local Media](17-autonomous-learning-portability-media.md)
18. [ECK-focused Community Learning](18-eck-focused-community-learning.md)
19. [Self-Evolution & Portable Skill Graph](19-self-evolution-and-skill-graph.md)
20. [Continuous Learning, Media Prompts & Themes](20-continuous-learning-media-and-themes.md)
21. [Media Commands & Artifact Quality](21-media-commands-and-artifact-quality.md)
22. [P0 Reliability Hardening](22-p0-reliability-hardening.md)
23. [P1 Command and Media Reliability](23-p1-command-and-media-reliability.md)

## v0.2 開發規格下載

- [DOCX](ECK_Digital_Life_Kernel_v0.2.0_Development_Specification_zh-TW.docx)
- [PDF（2026-08-02 基線）](ECK_Digital_Life_Kernel_v0.2.0_Development_Specification_zh-TW.pdf)

## 文件治理

重大架構選擇記錄於`docs/adr/`。Research想法不得直接成為正式規格；必須先定義成功條件、反證條件、實驗方法與資源上限。程式行為若與文件矛盾，變更不得合併，直到修正程式、文件或以ADR正式改變決策。
