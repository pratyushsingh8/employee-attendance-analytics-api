# REVIEW.md

This review records defects addressed during implementation and the checks used to verify them.

| # | Area | Defect or finding | How it was noticed | Resolution / status |
|---|---|---|---|---|
| 1 | `health()` | Reporting healthy without checking MongoDB could hide a database outage. | Test the health endpoint while MongoDB is unavailable. | Added a MongoDB ping and a 503 response on database errors. |
| 2 | `list_employees()` | Incorrect pagination offsets could skip the first page. | Request page 1 with a small page size. | Uses `(page - 1) * page_size`. |
| 3 | `list_employees()` | Filtered results and total counts must use the same department filter. | Compare filtered items and total for a department. | Applies the filter to both the query and count. |
| 4 | `list_employees()` | Unstable ordering or invalid pagination can make pages inconsistent. | Test ordering and invalid page/page-size values. | Added deterministic ordering and pagination validation. |
| 5 | `punch_in()` | An unknown employee should not cause an internal server error. | Submit a punch-in for an unknown employee. | Returns 404 when the employee does not exist. |
| 6 | Timestamp validation | Epoch milliseconds must be strict integers and interpreted consistently in IST/UTC. | Test invalid types and timestamps around midnight. | Added strict timestamp validation and timezone-aware conversion. |
| 7 | Attendance uniqueness | Separate duplicate checks and inserts alone do not prevent concurrent duplicate attendance records. | Inspect simultaneous-request behavior and the unique index. | Added a unique `(emp_code, date)` index and duplicate-key handling. The database uniqueness safeguard is verified; simultaneous HTTP requests were not stress-tested. |
| 8 | `compute_late_minutes()` | Truncating minutes before checking the grace period mishandles the 10-minute boundary. | Test 09:40 and 09:41 for a 09:30 shift. | Checks elapsed seconds against the 600-second grace period before calculating whole late minutes. Boundary tests passed. |
| 9 | `compute_work_hours()` | Built-in rounding does not guarantee decimal half-up behavior. | Test durations around a two-decimal rounding boundary. | Uses `Decimal` with `ROUND_HALF_UP`; boundary tests passed. |
| 10 | `compute_overtime()` | Overnight shifts and the 30-minute overtime threshold need explicit handling. | Test overtime just below and at 30 minutes across midnight. | Handles overnight shift ends and applies the minimum threshold. Tested boundary cases passed. |
| 11 | `create_employee()` | Concurrent employee creation could bypass an application-only duplicate check. | Inspect the unique index and duplicate-key response handling. | Added a unique `emp_code` index and duplicate-key handling. |
| 12 | `created_at` | A naive local timestamp can vary with the machine timezone. | Inspect timestamp creation and stored values. | Uses an explicitly UTC timestamp. |
| 13 | `list_attendance()` | Sorting and pagination in Python needlessly load matching records into application memory. | Inspect the query and test pagination. | Uses MongoDB sorting, skip, and limit. |
| 14 | `list_attendance()` | Invalid page values or excessive page sizes should be rejected. | Test page 0 and page size 101. | Validates page >= 1 and page size from 1 to 100. |
| 15 | API serialization | MongoDB internal `_id` should not leak into attendance responses. | Inspect returned JSON. | Excludes `_id` from attendance documents returned by the API. |
| 16 | Startup indexes | Missing indexes can weaken uniqueness guarantees and query performance. | Inspect collection indexes after startup. | Creates required indexes idempotently. |
| 17 | Regularization and analytics | Required endpoints were absent from the starter implementation. | Call each required route and test representative inputs. | Implemented regularization, monthly analytics, department summary, leaderboard, trend, and explain endpoints. Tested validation and representative results. |
| 18 | Analytics query plans | A correct result alone does not prove a query uses indexes efficiently. | Call the explain endpoint and inspect execution statistics. | Added explain support for the required query types and verified index scans on tested local data. This is not a substitute for testing the grader's full dataset. |

## Findings considered but not treated as defects

- A unique database index is necessary even when an application checks for duplicates first; the check alone is not concurrency-safe.
- Weekend attendance records can still contribute to late and overtime totals even though weekends are excluded from weekday-based present-day calculations.
- An employee with no attendance logs must remain in department headcount, so the department summary starts from employees.
- A leaderboard may return more rows than its requested limit when employees tie at the cutoff rank.
- Local explain results demonstrate behavior on the local dataset; they do not prove performance on every possible production-scale dataset.
