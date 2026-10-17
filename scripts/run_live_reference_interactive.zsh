#!/bin/zsh
set -euo pipefail
set +x

repo_root="${0:A:h:h}"
cd "$repo_root"

cleanup() {
  unset AZURE_TEXT_ENDPOINT AZURE_TEXT_API_KEY AZURE_TEXT_MODEL
  unset AZURE_IMAGE_ENDPOINT AZURE_IMAGE_API_KEY AZURE_IMAGE_MODEL
  unset DEEPSEEK_ENDPOINT DEEPSEEK_API_KEY DEEPSEEK_TEXT_MODEL
}
trap cleanup EXIT INT TERM

echo "Keys are read into this process only; they are not written to files."
read -s "AZURE_IMAGE_ENDPOINT?Azure image endpoint (hidden, HTTPS /openai/v1/images/generations): "
echo
read -s "AZURE_IMAGE_API_KEY?Azure image API key (hidden): "
echo
AZURE_IMAGE_MODEL="gpt-image-2"

read -s "AZURE_TEXT_ENDPOINT?Azure text endpoint (hidden, optional; press Enter for deterministic prompt composition): "
echo
if [[ -n "$AZURE_TEXT_ENDPOINT" ]]; then
  read -s "AZURE_TEXT_API_KEY?Azure text API key (hidden): "
  echo
  AZURE_TEXT_MODEL="gpt-5.6-sol"
else
  AZURE_TEXT_API_KEY=""
  AZURE_TEXT_MODEL="gpt-5.6-sol"
fi

export AZURE_IMAGE_ENDPOINT AZURE_IMAGE_API_KEY AZURE_IMAGE_MODEL
export AZURE_TEXT_ENDPOINT AZURE_TEXT_API_KEY AZURE_TEXT_MODEL

echo "This will make at most one text call and one high-quality 1024x1024 image call."
read "confirmation?Type GENERATE to continue: "
if [[ "$confirmation" != "GENERATE" ]]; then
  echo "Cancelled without a provider call."
  exit 2
fi

PYTHONPATH=src .venv/bin/python -m nining capabilities \
  --config config.creator.example.toml
PYTHONPATH=src .venv/bin/python -m nining generate-reference \
  --config config.creator.example.toml \
  --materials materials/sample

echo "Candidate generated. Review it in the local Story/God UI before any scene call:"
echo "PYTHONPATH=src .venv/bin/python -m nining serve --config config.creator.example.toml --materials materials/sample --port 0"
