"""Worker entry point; equivalent to `uv run python -m text2sql.jobs.worker`."""

from text2sql.jobs.worker import main

if __name__ == "__main__":
    main()
