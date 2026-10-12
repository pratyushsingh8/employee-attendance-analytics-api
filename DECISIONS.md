# DECISIONS.md

1. **Indexes.** I use a unique index on `employees.emp_code` to prevent duplicate employees, and a unique compound index on `attendance_logs.emp_code` and `date` to guarantee one attendance record per employee per day. The employee department/code index supports department queries, while the joined-date index supports headcount filtering. Attendance indexes support date-based analytics and employee/date lookups. I did not add a separate index for every possible filter because indexes increase storage and write costs.

2. **Punch-in race.** Both requests may pass the initial existence and duplicate checks. The unique compound attendance index is the final safeguard: only one insert can succeed. The other request receives a duplicate-key error, which the API converts to a conflict response instead of creating a second record.

3. **Ties.** The late leaderboard uses MongoDB `$rank` on total late minutes and filters to ranks at or below the requested limit. Employees tied at the cutoff are all included, so the result may contain more rows than the limit. Employee code provides deterministic display ordering.

4. **Headcount.** The department summary starts from employees and looks up attendance, rather than starting from attendance records. This preserves employees who have no logs. Employees are filtered by their joining date relative to the month end.

5. **100x the data.** I would benchmark the aggregation pipelines with a production-like dataset, inspect execution statistics, and tune indexes and query shapes based on measured bottlenecks. I would also consider archival or partitioning strategies if historical attendance volume became a problem.
