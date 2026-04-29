{
  description = "slopmortem — find similar dead startups, write per-candidate post-mortems";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }:
    flake-utils.lib.eachDefaultSystem (system: let
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };

      python = pkgs.python314;

      runtimeLibs = with pkgs;
        [
          stdenv.cc.cc.lib
          zlib
          openssl
          libxml2
          libxslt
        ]
        ++ pkgs.lib.optionals pkgs.stdenv.isLinux [
          glibc
        ];
    in {
      devShells.default = pkgs.mkShell {
        name = "slopmortem";

        packages = with pkgs; [
          python
          uv
          ruff
          basedpyright

          just

          pre-commit
          taplo

          claude-code

          nodejs_25

          docker-compose

          yq-go
          jq
          git
          curl
        ];

        env = {
          UV_PYTHON = "${python}/bin/python3.14";
          UV_PYTHON_DOWNLOADS = "never";
          UV_PROJECT_ENVIRONMENT = ".venv";

          HF_HOME = "./data/hf";
          SENTENCE_TRANSFORMERS_HOME = "./data/hf";
        };

        shellHook = ''
          export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath runtimeLibs}:''${LD_LIBRARY_PATH:-}"

          if [ ! -d .venv ]; then
            echo "→ creating .venv via uv"
            uv venv --python "${python}/bin/python3.14"
          fi

          if [ -f pyproject.toml ]; then
            uv sync --frozen 2>/dev/null || uv sync
          fi
        '';
      };
    });
}
