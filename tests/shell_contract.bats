#!/usr/bin/env bats
# SPDX-License-Identifier: AGPL-3.0-or-later

setup() {
    REPOSITORY_ROOT=$(CDPATH='' cd -- "$BATS_TEST_DIRNAME/.." && pwd)
    TEST_ROOT=$(mktemp -d)
    mkdir -p "$TEST_ROOT/bin"
    cat >"$TEST_ROOT/bin/lazy" <<'EOF'
#!/bin/sh
printf '<%s>\n' "$@" >>"$LAZYROS_TEST_LOG"
EOF
    chmod 755 "$TEST_ROOT/bin/lazy"
    export LAZYROS_TEST_LOG="$TEST_ROOT/lazy.log"
    : >"$LAZYROS_TEST_LOG"
}

teardown() {
    rm -rf -- "$TEST_ROOT"
}

@test "managed Bash init selects Bash and preserves argv boundaries" {
    run env \
        PATH="$TEST_ROOT/bin:$PATH" \
        LAZYROS_TEST_LOG="$LAZYROS_TEST_LOG" \
        bash --noprofile --norc -c \
        'source "$1"; lazy; lazy build "package one"' \
        _ "$REPOSITORY_ROOT/shell/lazy-init.bash"

    [ "$status" -eq 0 ]
    run grep -Fx '<--shell>' "$LAZYROS_TEST_LOG"
    [ "$status" -eq 0 ]
    run grep -Fx '<package one>' "$LAZYROS_TEST_LOG"
    [ "$status" -eq 0 ]
}

@test "source launcher reports the single VERSION value" {
    run env PYTHONPATH="$REPOSITORY_ROOT/src" "$REPOSITORY_ROOT/bin/lazy" --version
    [ "$status" -eq 0 ]
    [ "$output" = "lazy $(tr -d '\r\n' <"$REPOSITORY_ROOT/VERSION")" ]
}
