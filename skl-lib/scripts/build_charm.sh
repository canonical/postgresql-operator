#!/bin/bash

## This builds the whl and then copies it to all 4 test charms and updates the requirements file.

set -e

# --- Variables ---
PLATFORM=""
declare -a TEST_CHARMS=()

LIB="single_kernel_postgresql"
LIB_PATH="./${LIB}"
CHARMS_PATH="./tests/charms"
# This is for charms that don't need the library to be copied and can be packed directly.
THIRD_PARTY_CHARMS=("")
# We compute a version based on the tag of the pip package version.
# This is just for the test charms
VERSION_TAG="test/0.0.0+dirty"

# --- Argument Parsing ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -p|--platform)
            PLATFORM="$2"
            shift 2
            ;;
        -c|--charm)
            TEST_CHARMS+=("$2")
            shift 2
            ;;
        *)
            # Maintain backward compatibility for an unnamed first parameter as the charm
            if [[ "$1" != -* ]] && [ ${#TEST_CHARMS[@]} -eq 0 ]; then
                TEST_CHARMS+=("$1")
                shift
            else
                echo "Unknown parameter passed: $1"
                echo "Usage: $0 [-p|--platform <platform>] [-c|--charm <charm_path>] [charm_path]"
                exit 1
            fi
            ;;
    esac
done

# Default to postgresql_vm_test_charm if no charms were specified
if [ ${#TEST_CHARMS[@]} -eq 0 ]; then
    TEST_CHARMS=("${CHARMS_PATH}/postgresql_vm_test_charm")
fi

# --- Helper Functions ---
pack_charm() {
    # Store arguments in an array to safely handle spaces or empty strings
    local pack_args=("-v")

    # Inject platform argument if one was provided
    if [ -n "$PLATFORM" ]; then
        pack_args+=("--platform" "$PLATFORM")
    fi

    if ${CI_CACHE:-false}; then
        ccc pack "${pack_args[@]}"
    else
        charmcraft pack "${pack_args[@]}"
    fi
}


# --- Main Logic ---
git_hash=$(git describe --always --dirty)

for directory in "${TEST_CHARMS[@]}"; do

    # Pack the third party charms
    if [[ " ${THIRD_PARTY_CHARMS[*]} " =~ ${directory} ]]; then
        echo -e "Packing third party charm ${directory}\n"
        pushd "$directory"
        pack_charm
        popd
    else
        echo "clearing out libs for charm"
        directory_lib_path="${directory}/${LIB}"
        rm -rf "$directory_lib_path"
        mkdir "$directory_lib_path"

        echo "copying over libs from single kernel charm"
        cp -r "${LIB_PATH}/" "$directory_lib_path/"
        cp "pyproject.toml" "$directory_lib_path"
        cp "README.md" "$directory_lib_path"
        cp "LICENSE" "$directory_lib_path"

        echo -e "Building charm ${directory}\n"
        # break 

        pushd "$directory"

        # Backup files
        cp refresh_versions.toml refresh_versions.toml.backup
        cp pyproject.toml pyproject.toml.backup
        cp poetry.lock poetry.lock.backup

        sed -i "2s@^@charm = \"${VERSION_TAG}\"\n@" refresh_versions.toml

        # Disable strict mode for build test lib.
        pushd "${LIB_PATH}"
        git init
        sed 's/strict = true/strict = false/' -i "pyproject.toml"
        popd

        poetry add "${LIB_PATH}/"
        poetry lock


        # Pack the charm
        pack_charm

        # Cleanup
        echo "removing copied files from single kernel charm."
        rm -rf "${LIB_PATH}"
        mv pyproject.toml.backup pyproject.toml
        mv poetry.lock.backup poetry.lock
        mv refresh_versions.toml.backup refresh_versions.toml

        # Go back to root directory
        popd
    fi
done