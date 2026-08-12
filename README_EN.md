# ZeroOne Skills

[中文](README.md) | [English](README_EN.md)

ZeroOne Skills is an open-source skill repository for AI Agents.

We turn methods, decision criteria, and operational workflows from real work into Skills that Agents can understand, execute, and reuse. The repository currently focuses on information monitoring and industry intelligence, with more engineering and productivity use cases to come.

## Explore Skills

### Intelligence

| Skill | In one sentence |
| --- | --- |
| [monitor-anything](skills/intelligence/monitor-anything/README_EN.md) | Define what matters in natural language and receive a daily visual report built from filtering, cross-source clustering, and summarization. |
| [industry-insight](skills/intelligence/industry-insight/SKILL.md) | Turn an information feed into an inspectable, human-guided industry intelligence pipeline. |

## How to read this repository

1. **Browse by category** and use the one-line descriptions to find a relevant skill.
2. **Start with `README.md`** for the problem, intended use, and getting-started guidance.
3. **Read `SKILL.md`** for Agent triggers, workflow steps, and execution boundaries.
4. **Go deeper when needed**: `ARCHITECTURE.md`, `references/`, `prompts/`, and `scripts/` cover architecture, contracts, prompts, and deterministic implementation.

Each skill is a largely self-contained directory. You only need to read or install the skill that interests you—not understand the entire repository.

```text
skills/
├── intelligence/   # Monitoring, industry intelligence, and research
├── engineering/    # Engineering workflows — in development
└── productivity/   # General productivity — in development
```

Refer to each skill's own README for installation and usage details.

## About the team

The ZeroOne Skills team works on AI Agents and data intelligence products. Our goal is to turn workflows proven in real tasks into clear, composable, and continuously evolving open capabilities—not one-off prompts.

## Contributing

Issues and pull requests for bug reports, documentation improvements, and new skills are welcome.

## License

[MIT License](LICENSE)
