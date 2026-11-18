"""Pinned CORS + real mux witness; logging/metrics/tracing are reachability probes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

BASE = "d0300073f685bb1154f7437f3172799d31bfddbe"
HEAD = "c1a26ade6db7da96622c9bdca7674ef7094c122f"
FILES = ("pkg/gofr/http/middleware/cors.go", "pkg/gofr/http/router.go",
         "pkg/gofr/http_server.go", "go.mod")
MAIN = r'''package main
import ("fmt"; "net/http"; "net/http/httptest"; "github.com/gorilla/mux")
func main() {
 for _, method := range []string{"OPTIONS", "GET"} {
  for _, order := range []string{"base", "head"} {
   logged, handled := 0, 0
   probe := func(next http.Handler) http.Handler { return http.HandlerFunc(func(w http.ResponseWriter,r *http.Request) { logged++; next.ServeHTTP(w,r) }) }
   identity := func(next http.Handler) http.Handler { return next }
   routes := []string{"GET", "OPTIONS"}
   r := mux.NewRouter()
   if order == "base" { r.Use(identity,probe,CORS(nil,&routes),identity) } else { r.Use(identity,CORS(nil,&routes),identity); r.Use(probe) }
   r.HandleFunc("/example",func(w http.ResponseWriter,r *http.Request){handled++;w.WriteHeader(200)}).Methods("GET","OPTIONS")
   w := httptest.NewRecorder()
   req := httptest.NewRequest(method,"http://example.test/example",nil)
   req.Header.Set("Origin","https://other.test")
   req.Header.Set("Access-Control-Request-Method","GET")
   r.ServeHTTP(w,req)
   want := 1
   if method == "OPTIONS" && order == "head" { want = 0 }
   if logged != want || w.Code != 200 { panic("unexpected middleware behavior") }
   fmt.Printf("%s %s status=%d logging_reached=%d handler_reached=%d\n",order,method,w.Code,logged,handled)
  }
 }
}
'''


def snapshot_name(revision, file):
    return revision + "__" + file.replace("/", "__") + ".txt"


def run(repo, output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    hashes = {}
    sources = {}
    for revision in (BASE, HEAD):
        for file in FILES:
            text = subprocess.run(["git", "show", f"{revision}:{file}"], cwd=repo,
                                  capture_output=True, text=True, check=True).stdout
            name = snapshot_name(revision, file)
            (root / name).write_text(text)
            hashes[name] = hashlib.sha256(text.encode()).hexdigest()
            sources[revision, file] = text
    cors = FILES[0]
    if sources[BASE, cors] != sources[HEAD, cors]:
        raise ValueError("CORS source changed; paired witness assumption invalid")
    (root / "cors.go").write_text(sources[HEAD, cors].replace("package middleware", "package main", 1))
    (root / "main.go").write_text(MAIN)
    (root / "go.mod").write_text("module witness\n\ngo 1.24\n\nrequire github.com/gorilla/mux v1.8.1\n")
    env = dict(os.environ, GOWORK="off", GOPROXY="off", GOSUMDB="off", GOTOOLCHAIN="local")
    result = subprocess.run(["go", "run", "-mod=mod", "."], cwd=root, env=env,
                            capture_output=True, text=True, timeout=60)
    receipt = {"base": BASE, "head": HEAD, "source_hashes": hashes,
               "command": "go run -mod=mod .", "network": "GOPROXY=off GOSUMDB=off",
               "go_version": subprocess.run(["go", "version"], capture_output=True,text=True).stdout.strip(),
               "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
               "scope": "exact unchanged CORS, mux v1.8.1; logging replaced by reachability counter; tracing/metrics identity; manually transcribed base/head registration"}
    receipt["generated_hashes"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in root.iterdir() if p.name in ("main.go", "cors.go", "go.mod", "go.sum")}
    (root / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    if result.returncode:
        raise RuntimeError("Witness failed; artifacts retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("output")
    args = parser.parse_args()
    run(args.repo, args.output)
