# ZeroOne Skills

[中文](README.md) | [English](README_EN.md)

An open-source collection of AI Agent skills for information monitoring, industry intelligence, and reusable business workflows.

Each skill packages its instructions, prompts, deterministic scripts, and supporting assets in a self-contained directory. Copy the skill you need into a product that supports Agent Skills, then trigger the workflow in natural language.

## Skills

| Skill | What it does | Main output |
| --- | --- | --- |
| [monitor-anything](skills/intelligence/monitor-anything/README_EN.md) | Turns a natural-language monitoring brief into collection, cleaning, semantic filtering, cross-source clustering, summarization, and rendering | A standalone daily HTML report and a local history index, in Chinese or English |
| [industry-insight](skills/intelligence/industry-insight/SKILL.md) | Filters an information feed by user-defined interests, clusters related coverage, and decides which multi-source topics are worth publishing | Structured industry intelligence and a standalone dashboard |

The two skills are presented as peers. Choose `monitor-anything` for recurring subscriptions and automated reports, or `industry-insight` for a more inspectable, hands-on intelligence pipeline.

## Installation

### Copy from this repository

```bash
git clone https://github.com/g453030291/zeroone-skills.git
cd zeroone-skills

# Copy the skill you need. Replace the destination with your Agent's Skills directory.
cp -R skills/intelligence/monitor-anything /path/to/your/skills/
cp -R skills/intelligence/industry-insight /path/to/your/skills/
```

The Skills directory and loading mechanism vary by Agent product. Follow your product's documentation. You only need to copy the skill you intend to use, not the entire repository.

### Install monitor-anything from SkillHub

`monitor-anything` is also published on [SkillHub](https://skillhub.cn/team-skills/monitor-anything). If the SkillHub CLI is already available, run:

```bash
skillhub install monitor-anything --dir <your Agent's Skills directory>
```

`--dir` must point to the directory from which your Agent actually loads skills. If the CLI is not installed, follow the [SkillHub installation guide](https://skillhub.cn/install/skillhub.md), or give that link directly to your Agent.

## Usage

After installation, describe the job in natural language. Your Agent will use each skill's trigger rules to select and run the workflow.

For example:

> Track China's AI inference infrastructure and chip supply chain every day. Exclude funding gossip and course promotions.

> Summarize the latest developments in the electric vehicle industry, but only publish events corroborated by independent sources.

See each skill's `README.md` or `SKILL.md` for initial setup, automation, data boundaries, and detailed usage.

## Repository structure

```text
zeroone-skills/
├── skills/
│   └── intelligence/
│       ├── monitor-anything/
│       └── industry-insight/
├── LICENSE
├── README.md
└── README_EN.md
```

A skill commonly contains:

- `SKILL.md` — trigger rules and the Agent workflow
- `scripts/` — deterministic networking, storage, and transformation tasks
- `prompts/` or `references/` — semantic task instructions and output contracts
- `assets/` — static resources such as report templates

The exact structure is documented inside each skill directory.

## Contributing

Issues and pull requests for bug reports, documentation improvements, and new skills are welcome. Keep each change focused, and verify that example commands, relative links, and data-boundary statements match the implementation.

## License

This project is available under the [MIT License](LICENSE).
