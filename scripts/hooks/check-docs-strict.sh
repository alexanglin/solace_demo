#!/usr/bin/env sh
# Strict documentation check -- runs at the pre-commit stage.
#
# Bans words used as if they were specifications. A threshold with no number is
# not a specification; it is a gap that cannot fail a gate. The review found this
# to be the single largest defect class in the planning documents.
#
# Run with:  just lint-docs-strict
set -eu

status=0
pattern='the chosen|deterministic thresholds|latency budget|as needed|as appropriate|where appropriate|an appropriate|suitable|reasonable'

for file in "$@"; do
	# docs/adr/* is immutable by policy (see docs/adr/README.md), so a banned phrase inside a
	# rationale or a quotation cannot be fixed by editing it. CONTRIBUTING.md documents this very
	# check and necessarily contains the words it bans.
	case "${file#./}" in
	docs/adr/*) continue ;;
	CONTRIBUTING.md) continue ;;
	esac
	if grep -nEi "$pattern" "$file" >/dev/null 2>&1; then
		printf '%s\n' "$file" >&2
		grep -nEi "$pattern" "$file" | sed 's/^/    /' >&2
		status=1
	fi
done

if [ "$status" -ne 0 ]; then
	cat >&2 <<'MSG'

Replace each with a number and a unit, or mark it explicitly:
    (provisional -- confirm in Phase 0)
Numbers belong in the operating-parameters document so they have one home.
MSG
fi

exit "$status"
