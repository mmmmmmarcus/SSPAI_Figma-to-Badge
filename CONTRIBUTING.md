# Contributing

Use Python 3.10+, run setup, then `python -m unittest discover -s tests -v` in the venv. Run `BADGEGEN_INTEGRATION=1 .venv/bin/python -m unittest discover -s tests -v` to exercise Blender exports after setup. Run the gold/silver demo batch when changing materials/export logic. Keep outputs out of Git.

Add tests for behavior (provenance rejection, failed build preserving old outputs, container checks, geometry/normal regressions), not wording. New geometry families need reproducible original templates and actual GLB/USDZ validation plus native viewer review. Label experimental profiles honestly. Submit only code and assets you can license under MIT. Never contribute private designs, Figma responses, API tokens, or machine-specific config.

CI runs pure-Python workflow tests on Linux. A passing CI does not imply Blender or Apple viewer validation. Blender integration is an explicit local test.
