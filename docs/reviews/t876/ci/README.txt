# CI FAILED sets, extracted by me via: gh run view <id> --log | grep -oE 'FAILED tests/[^ ]+' | sort -u

34805162922: 29 failures
34811195620: 30 failures
34813482167: 30 failures
34813485141: 29 failures

## d70b55e run 34813485141 vs main 34805162922 -- NEW:
  (none -- identical sets)
## same-SHA d70b55e runs differ by:
23a24
> tests/test_t683_session_adapters.py::test_wake_delivery_is_deduped_by_message_id
