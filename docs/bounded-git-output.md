# Bounded Git tool output

git.inspect accepts max_output_bytes (strict1..1000000, default200000) for
stdout and stderr separately; retained raw bytes, not decoded text length.
UTF-8 replacement may expand partial characters. truncated=true reports loss.
Timeout defaults to30seconds, configurable0.1..300 on inspection calls.
Existing git.commit uses the same default bound/timeout. A commit timeout or
cancellation may follow a completed write: outcome unknown, inspect before
retrying. No automatic replay. POSIX process groups killed on timeout/cancel;
Windows only kills the direct child. Existing path safety remains unchanged.
Git config, filters/hooks and executable selection remain trusted inputs.
