import json
import subprocess
import sys


def call_mcp_tool(server_cmd, tool_name, arguments):
    """Simple MCP client simulator to call a tool over stdio JSON-RPC."""
    process = subprocess.Popen(
        server_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # Avoid pipe deadlocks from unconsumed stderr.
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=0,
    )

    # 1. Initialize
    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-client-sim", "version": "1.0.0"},
        },
    }
    process.stdin.write(json.dumps(init_request) + "\n")

    # Read responses until we get the init result
    while True:
        line = process.stdout.readline()
        if not line:
            break
        resp = json.loads(line)
        if resp.get("id") == 1:
            break

    # 2. Call Tool
    call_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    process.stdin.write(json.dumps(call_request) + "\n")

    # Read tool response
    result = None
    while True:
        line = process.stdout.readline()
        if not line:
            break
        resp = json.loads(line)
        if resp.get("id") == 2:
            result = resp
            break

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 call_mcp.py <tool_name> [json_args]")
        sys.exit(1)

    tool_name = sys.argv[1]
    arguments = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    server_command = [sys.executable, "mcp_server.py"]
    # Ensure mcp_server.py is in the current directory or provide full path

    response = call_mcp_tool(server_command, tool_name, arguments)
    print(json.dumps(response, indent=2))
