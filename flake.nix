{
  description = "slopmortem — find similar dead startups, write per-candidate post-mortems";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
    pyproject-nix,
    uv2nix,
    pyproject-build-systems,
  }:
    flake-utils.lib.eachDefaultSystem (system: let
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };

      python = pkgs.python314;

      workspace = uv2nix.lib.workspace.loadWorkspace {workspaceRoot = ./.;};

      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };

      pyprojectOverrides = final: prev: {
        # py-rust-stemmers (transitive via fastembed) ships sdist only — no
        # cp314 wheel — so it's built from source with maturin + cargo here.
        py-rust-stemmers = prev.py-rust-stemmers.overrideAttrs (old: {
          nativeBuildInputs =
            (old.nativeBuildInputs or [])
            ++ [
              pkgs.cargo
              pkgs.rustc
              pkgs.rustPlatform.cargoSetupHook
            ]
            ++ final.resolveBuildSystem {maturin = [];};
          cargoDeps = pkgs.rustPlatform.fetchCargoVendor {
            inherit (old) src;
            name = "py-rust-stemmers-${old.version}-cargo-deps";
            hash = "sha256-ton9uOTuje2A2ATNp0uNfr/NuXDxtbOPpz0Nie9mACs=";
          };
        });
      };

      pythonSet = (pkgs.callPackage pyproject-nix.build.packages {inherit python;})
        .overrideScope (
        pkgs.lib.composeManyExtensions [
          pyproject-build-systems.overlays.default
          overlay
          pyprojectOverrides
        ]
      );

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
      packages.default = pythonSet.mkVirtualEnv "slopmortem-env" workspace.deps.default;

      apps.default = {
        type = "app";
        program = "${self.packages.${system}.default}/bin/slopmortem";
      };

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

          docker
          docker-compose

          yq-go
          jq
          git
          git-lfs
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
