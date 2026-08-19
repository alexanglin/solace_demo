# Aerial Rescue Mesh -- canonical command entrypoints.
#
# Hooks and CI call the scripts under scripts/ directly, NOT these recipes, so
# neither depends on `just` being installed. These are the human-facing names.

# Show available recipes.
default:
    @just --list

# One-time developer setup.
install:
    pre-commit install --install-hooks
    @echo "Hooks installed for: pre-commit, commit-msg, pre-push, post-checkout, post-merge, pre-merge-commit"

# Everything CI runs, in the same order.
check: check-commit check-push

# Fast tier -- what runs on every commit.
check-commit:
    pre-commit run --all-files --hook-stage pre-commit

# Thorough tier -- what runs before every push.
check-push:
    pre-commit run --all-files --hook-stage pre-push

# Verify the mandatory AAA structure of every project-owned executable test.
check-aaa:
    pre-commit run --all-files --hook-stage pre-commit test-aaa

# Validate the complete schema and golden-fixture inventory without network access.
check-contracts:
    pre-commit run --all-files --hook-stage pre-commit contract-artifacts

# Apply every automatic fix. The only thing here that modifies files.
fix:
    scripts/fix.sh

# Regenerate architecture PNGs from their Graphviz sources and refresh hashes.
diagrams:
    scripts/diagrams.sh

# Strict documentation check. Blocking at pre-commit since 2026-08-19.
lint-docs-strict:
    pre-commit run --all-files docs-strict

# Refresh pinned hook revisions. Review every change before committing.
update-hooks:
    pre-commit autoupdate

# Remove hook caches and generated artifacts.
clean:
    pre-commit clean
    rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
