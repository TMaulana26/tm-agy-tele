# Headless Telegram Bot Operational Rules

This repository and VPS environment runs headless Antigravity CLI sessions connected to a Telegram Bot.

## Critical Operational Rules

1. **Strictly Avoid Internal `schedule` Tool**:
   - DO NOT invoke the internal `schedule` tool (e.g. cron expressions, background timers, or daemon crons).
   - In headless print mode (`agy -p`), background tasks started by `schedule` prevent the turn from closing and cause the subprocess to hang indefinitely.
   - For all scheduled tasks, recurring jobs, or cron requests: ALWAYS write standard shell scripts or python scripts and register them directly to the host Linux `crontab` via terminal commands (`crontab -l`, `crontab <file>`) or configure a `systemd` service/timer on the host.

2. **Synchronous Turn Completion**:
   - Complete all file modifications, script generations, and terminal executions synchronously within the current turn.
   - Never spawn detached background loops or indefinite timers from within an agent turn.

3. **Production Safety & Non-Interactive Execution**:
   - The bot runs non-interactively without a desktop UI. All outputs must be final and concise.
   - Do not request interactive browser sessions or UI-dependent tools.
