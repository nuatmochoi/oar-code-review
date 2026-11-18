"""Run exact PR-head URI functions in a stdlib-only mechanism harness."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

COMMIT = "dc0b3e630423b72cb9b2f273fdc18a5c11d12984"
FILE = "pkg/gofr/datasource/mongo/mongo.go"
MAIN = r'''
func main() {
 c := &Config{URI:"mongodb://admin:password@localhost:27017/mydb"}
 _, host, err := generateMongoURI(c)
 if err != nil || host != "localhost" || c.Host != "" { panic("URI fixture invalid") }
 fmt.Printf("uri_only config.Host=%q generated_host=%q\n", c.Host, host)
 c = &Config{Host:"localhost",Port:27017,Database:"mydb",User:"admin",Password:"p@ss"}
 uri, _, err := generateMongoURI(c)
 if err != nil { panic(err) }
 parsed, err := url.Parse(uri)
 if err != nil { panic(err) }
 password, _ := parsed.User.Password()
 if password != "p%40ss" { panic("double-encoding witness not reproduced") }
 fmt.Printf("encoded_uri=%s decoded_password=%q original=%q\n",uri,password,c.Password)
 _, err = getDBHost("mongodb+srv://cluster.example.com/db")
 if err != errIncorrectURI { panic("SRV rejection not reproduced") }
 fmt.Printf("srv_rejection=%v\n",err)
 host, err = getDBHost("mongodb://host1:27017,host2:27017/db")
 fmt.Printf("multi_host_result=%q error=%v\n",host,err)
}
'''


def extract(lines, start, end, expected_prefix):
    if not lines[start - 1].startswith(expected_prefix):
        raise ValueError("Pinned source range mismatch")
    return "\n".join(lines[start - 1:end])


def run(repo, output):
    source = subprocess.run(["git", "show", f"{COMMIT}:{FILE}"], cwd=repo,
                            capture_output=True, text=True, check=True).stdout
    lines = source.splitlines()
    ranges = [(30, 39, "type Config struct"), (43, 48, "var ("),
              (130, 181, "func generateMongoURI")]
    pieces = [extract(lines, *span) for span in ranges]
    code = 'package main\nimport ("errors";"fmt";"net/url";"time")\n' + "\n".join(pieces) + MAIN
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    with (root / "main.go").open("x") as stream:
        stream.write(code)
    result = subprocess.run(["go", "run", "main.go"], cwd=root,
                            capture_output=True, text=True, timeout=60)
    receipt = {"commit": COMMIT, "file": FILE, "ranges": ranges,
               "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "harness_sha256": hashlib.sha256(code.encode()).hexdigest(),
               "go_version": subprocess.run(["go", "version"], capture_output=True,
                                             text=True, check=True).stdout.strip(),
               "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
               "scope": "exact head helper functions, not driver/server/full repository execution"}
    with (root / "receipt.json").open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(json.dumps(receipt, indent=2))
    if result.returncode:
        raise RuntimeError("Witness failed; receipt retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("output")
    args = parser.parse_args()
    run(args.repo, args.output)
