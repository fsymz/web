Two legacy checks require source material outside this repository:

1. `tests/public-destinations.test.js` invokes a migration script whose
   input is hard-coded at `../院内导航_模拟导航整合版/.../routes.js`.
2. `tests/python/test_audit_read_only.py` invokes connectivity auditing
   against the absent authoritative high-resolution floor-map directory.

The workflow excludes only those environment-bound checks. It still runs
all other Python tests, all other top-level Node tests, route endpoint and
metric validation, syntax validation, bundle freshness, and the complete
candidate-release gate. Strict patient-release authorization remains off.
