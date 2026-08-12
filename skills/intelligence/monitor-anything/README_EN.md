# monitor-anything

[中文](README.md) | [English](README_EN.md) | [Back to ZeroOne Skills](../../../README_EN.md)

Describe what matters in one sentence and let your AI Agent turn scattered updates into a focused visual report every day.

`monitor-anything` runs a complete information pipeline:

**Collect ① → Clean ② → Semantically filter ③ → Cluster across sources ④ → Summarize ⑤ → Render ⑥**

It is designed for Agent products that support Skills, including Claude Code, Codex, and Cowork. Python scripts handle networking, storage, and format conversion. The active Agent performs filtering, clustering, and summarization from the included prompts, so no separate LLM API key is required.

## What you get

- **A monitoring brief in plain language** — describe what you want and what to exclude instead of maintaining complex rules.
- **A multi-channel information pool** — sources include WeChat Official Accounts, Xiaohongshu, The New York Times, and AI trend feeds. Actual availability depends on the upstream data service.
- **Cross-source event clustering** — coverage from independent accounts or domains is grouped into a single event.
- **Judgment, not a title dump** — reports explain what happened, why it matters to you, and how sources differ.
- **Chinese or English output** — report content, interface copy, and source labels follow the configured language.
- **Standalone daily reports** — each day produces a shareable HTML file plus a local history index.

This skill is intended for daily incremental reading. It is not a historical search engine, a permanent full-text archive, or a data-asset platform.

## Quick start

### Option 1: Install from SkillHub

Versions and release details are available on [SkillHub](https://skillhub.cn/team-skills/monitor-anything). If the SkillHub CLI is already installed, run:

```bash
skillhub install monitor-anything --dir <your Agent's Skills directory>
```

`--dir` must point to the directory from which your Agent actually loads skills. If the CLI is not installed, follow the [SkillHub installation guide](https://skillhub.cn/install/skillhub.md), or give that guide directly to your Agent.

### Option 2: Copy from the repository

```bash
git clone https://github.com/g453030291/zeroone-skills.git
cp -R zeroone-skills/skills/intelligence/monitor-anything /path/to/your/skills/
```

Replace the destination with the Skills directory used by your Agent product. After installation, tell your Agent:

> Track China's AI inference infrastructure and chip supply chain. Exclude funding gossip and course promotions.

On first use, the Agent will:

1. Check the data-service token and, if none exists, try to provision a 30-day trial token automatically.
2. Infer or ask about your interests, exclusions, report language, delivery time, and retention period.
3. Show the inferred brief verbatim and wait for confirmation before saving it.
4. Fetch real data and generate your first report.
5. If the host supports scheduled wake-ups, try to create separate collection and reporting tasks.

Trial tokens are not renewed automatically after expiry. A valid token must then be obtained or extended through the project's release channel.

## How it works

| Stage | Performed by | Purpose |
| --- | --- | --- |
| ① Collect | Python scripts | Fetch the latest 24 hours of incremental content |
| ② Clean | Python scripts | Normalize, deduplicate, and store content in local SQLite |
| ③ Semantically filter | AI Agent | Judge relevance against the monitoring brief |
| ④ Cluster across sources | AI Agent | Merge independent coverage of the same event |
| ⑤ Summarize | AI Agent | Produce headlines, summaries, relevance notes, and an overview |
| ⑥ Render | Python scripts | Generate HTML reports, the history index, and a sharing link |

If a monitor has no matches after filtering, the Agent may run one supplementary search, still limited to results from the latest 24 hours. If nothing qualifies after that attempt, the report states that honestly instead of inventing content.

See [SKILL.md](SKILL.md) for the complete Agent procedure and [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions and file responsibilities.

## Automation

The skill has no resident background process and does not modify system `cron` or `launchd` configuration. Recurring execution depends on the host Agent product's scheduled wake-up capability.

When supported, Setup attempts to create two task types:

- **Collection task** — fetches incremental content at 00:00, 06:00, 12:00, and 18:00 by default. Success is silent; failures produce one concise alert.
- **Report task** — runs filtering, clustering, summarization, rendering, and sharing once a day at 08:00 by default, then sends one final result notification.

Times use the execution environment's timezone. If the host does not support automation, the Agent will explain the limitation, and you can still request a fresh report manually at any time.

## Reports and local files

Local state is stored under the skill's `data/` directory by default:

```text
data/
├── config.json                 # Monitors, language, schedule, token, and other settings
├── monitor.db                  # Source content, processing state, and run history
└── reports/
    ├── YYYY-MM-DD.json         # Structured daily report
    ├── YYYY-MM-DD.html         # Standalone daily report page
    ├── dashboard.html          # Local history index
    └── dates-manifest.js       # Compact date summaries used by the index
```

The repository's `.gitignore` excludes `data/`; only `.gitkeep`, which preserves the directory, is tracked. Data is retained for 30 days by default, and expired records are cleaned during collection runs.

## Data, sharing, and privacy

Understand these boundaries before use:

- Source content, configuration, and intermediate results are stored locally under `data/` by default.
- Scripts connect to Lingyi Labs services to provision a trial token, fetch content, perform a supplementary search when needed, and upload reports.
- Each standalone daily report, `YYYY-MM-DD.html`, is uploaded to the Lingyi Labs sharing service by default after generation. The resulting URL is public to anyone who has it. Only that day's report is uploaded—not the history index or other dates.
- Daily report pages include Baidu Analytics. Opening a report causes the browser to send a standard visit event to `hm.baidu.com`; the report body is not sent as analytics event content. `dashboard.html` does not contain this analytics script.
- The token remains in local configuration and is never embedded in shareable HTML.
- Trial-token request limits are calculated by client IP. Once created, a token is not bound to that IP.

If default public report uploads or page analytics are unacceptable, do not use the default sharing flow without first adjusting the implementation and workflow.

## Useful diagnostic commands

Run these commands from the `monitor-anything` directory:

```bash
# Check the token or provision a trial token automatically
python3 scripts/setup.py check-token

# Fetch the latest incremental content manually
python3 scripts/harvest.py run

# Inspect recent collection status
python3 scripts/harvest.py status
```

A complete report is not a single script command: stages ③, ④, and ⑤ require an AI Agent to read `prompts/` and make semantic judgments. For normal use, invoke the skill through your Agent instead of attempting to bypass it and assemble the entire pipeline manually.

## Configuration and requirements

- Python 3
- Python standard library only; no `pip install` required
- Report language: `zh` or `en`
- Default report time: `08:00`
- Default collection hours: `0 / 6 / 12 / 18`
- Default retention: 30 days

Initial Setup writes configuration to `data/config.json`. Long-lived tokens should be passed to the setup command through standard input so they do not appear in shell history or process listings; [SKILL.md](SKILL.md) defines the exact procedure.

## Directory structure

```text
monitor-anything/
├── SKILL.md
├── ARCHITECTURE.md
├── README.md
├── README_EN.md
├── scripts/
├── prompts/
├── assets/
└── data/
```

## License

Distributed with ZeroOne Skills under the [MIT License](../../../LICENSE).
