#!/bin/bash
set -euo pipefail

cd "$(dirname "$(realpath "$0")")"

help() {
    echo "Usage: $0 [-v VERSION] [-i] [-p HOST]..."
    echo "Build a wheel for the latest commit, and optionally install or push"
    echo
    echo "Options:"
    echo "  -v VERSION: create a new tag VERSION and build that"
    echo "  -i: install the built wheel locally with uv"
    echo "  -p HOST: push the built wheel to the given host and install with uv"
}

version=
install=
push_hosts=()
while getopts ":v:ip:h" opt; do
    case $opt in
        v)
            version="$OPTARG"
            ;;
        i)
            install=y
            ;;
        p)
            push_hosts+=("$OPTARG")
            ;;
        h)
            help
            exit 0
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            exit 1
            ;;
        :)
            echo "Option -$OPTARG requires an argument." >&2
            exit 1
            ;;
    esac
done

if [ -n "$version" ]; then
    if output="$(git status --porcelain)" && [ -z "$output" ]; then
        git tag "$version"
    else
        echo "The working directory is dirty. Please commit any pending changes." >&2
        exit 1
    fi
fi

ecode=0
rm -f dist/*.whl
uv  build
wheel="$(basename "$(ls dist/*.whl)")"

if [ -n "$install" ]; then
    echo "Installing $wheel with uv"
    uv tool install "dist/$wheel" --force || ecode=1
fi

for host in "${push_hosts[@]}" ; do
    echo "Pushing $wheel to $host"
    if ! scp "dist/$wheel" "$host:~"; then
        ecode=1
        continue
    fi
    echo "Installing $wheel with uv on $host"
    ssh -t "$host" "bash -l -c 'uv tool install ~/${wheel@Q} --force'" || ecode=1
done

exit "$ecode"
