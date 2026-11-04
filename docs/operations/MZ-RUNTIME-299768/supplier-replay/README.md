# Supplier confirmation lost before delivery

A private MongoDB and two temporary loopback Uvicorn processes exercised the real supplier dispatch and employee workspace routes via the repository benchmark fixture. Process A persisted the supplier file, then withheld its first successful HTTP response before sending headers. Process B could already read the file. After A received SIGKILL, its client received a transport error. B returned the existing file for the byte-identical retry. The [recorded run](results.json) contains one dispatch, one dispatch event and one piece event. Orders and workspace reads on B succeeded across all four barriers. Uvicorn 0.34.0 was used for this local test.

```sh
bash docs/operations/MZ-RUNTIME-299768/supplier-replay/run.sh "$repo" 188b1c5b0515d081ad4ebef3f6b7a44998a9fe0e "$mongod" "$python" evidence.json
```

The result is limited to loss **after** commit. Incomplete mid-write state and the actual store file require readback with their own identifiers.
