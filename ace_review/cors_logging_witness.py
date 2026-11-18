"""Actual pinned Logging/CORS/backend integration, without booting full GoFr server."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from .cors_order_witness import BASE, HEAD

FILES = ("pkg/gofr/http/middleware/cors.go", "pkg/gofr/http/middleware/logger.go",
         "pkg/gofr/logging/logger.go", "pkg/gofr/logging/level.go", "pkg/gofr/version/version.go")
MAIN = r'''package main
import ("encoding/json";"fmt";"os";"net/http";"net/http/httptest";"strings";
"github.com/gorilla/mux";"gofr.dev/pkg/gofr/logging";"gofr.dev/pkg/gofr/http/middleware")
func main() {
 for _, routeCase := range []string{"options_matched","get_only"} {
 for _, method := range []string{"OPTIONS","GET"} {
  for _, order := range []string{"before","after"} {
   path := routeCase+"_"+method+"_"+order+".jsonl"
   logger := logging.NewFileLogger(path)
   r := mux.NewRouter()
   routes := []string{"GET","OPTIONS"}
   logMiddleware := middleware.Logging(logger)
   cors := middleware.CORS(nil,&routes)
   if order == "before" {r.Use(logMiddleware,cors)} else {r.Use(cors);r.Use(logMiddleware)}
   route := r.HandleFunc("/example",func(w http.ResponseWriter,r *http.Request){w.WriteHeader(200)})
   if routeCase == "options_matched" {route.Methods("GET","OPTIONS")} else {route.Methods("GET")}
   req := httptest.NewRequest(method,"http://example.test/example",nil)
   req.Header.Set("Origin","https://other.test")
   req.Header.Set("Access-Control-Request-Method","GET")
   w := httptest.NewRecorder()
   r.ServeHTTP(w,req)
   data,err := os.ReadFile(path); if err != nil {panic(err)}
   count := 0
   for _,line := range strings.Split(strings.TrimSpace(string(data)),"\n") {
    if line == "" {continue}
    var entry struct{Message struct{Method string; Response int}}
    if err := json.Unmarshal([]byte(line),&entry); err != nil {panic(err)}
    if entry.Message.Method != method || entry.Message.Response != 200 {panic("wrong log content")}; count++
   }
   want := 1; if method == "OPTIONS" && order == "after" {want=0}
   status := 200
   if routeCase == "get_only" && method == "OPTIONS" {want=0;status=405}
   if count != want || w.Code != status {panic("log regression expectation failed")}
   fmt.Printf("route=%s %s order=%s status=%d actual_request_logs=%d\n",routeCase,method,order,w.Code,count)
  }
 }
 }
}
'''


def run(repo, output, actual_router=False):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    records = []
    for label, revision in (("base", BASE), ("head", HEAD)):
        folder = root / label
        folder.mkdir()
        hashes = {}
        extra = ("pkg/gofr/http/router.go",) if actual_router else ()
        for file in (*FILES, *extra, "go.mod", "go.sum"):
            value = subprocess.run(["git", "show", f"{revision}:{file}"], cwd=repo,
                                   capture_output=True, text=True, check=True).stdout
            path = folder / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
            hashes[file] = hashlib.sha256(value.encode()).hexdigest()
        main = router_main() if actual_router else MAIN
        (folder / "main.go").write_text(main)
        env = dict(os.environ, GOWORK="off", GOPROXY="off", GOSUMDB="off", GOTOOLCHAIN="local")
        result = subprocess.run(["go", "run", "-mod=readonly", "."], cwd=folder,
                                capture_output=True, text=True, env=env, timeout=60)
        record = {"revision": revision, "label": label, "source_hashes": hashes,
                  "harness_sha256": hashlib.sha256(main.encode()).hexdigest(),
                  "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
                  "logs": {p.name: p.read_text() for p in folder.glob("*.jsonl")}}
        records.append(record)
        print(json.dumps({k: record[k] for k in ("label", "exit_code", "stdout", "stderr")}), flush=True)
    receipt = {"records": records,
               "go_version": subprocess.run(["go","version"],capture_output=True,text=True).stdout.strip(),
               "scope": "Exact revision Logging/CORS/NewFileLogger and original dependencies. Manual middleware registration; Tracer/Metrics omitted; not full App startup.",
               "actual_gofr_router": actual_router,
               "command": "go run -mod=readonly .", "dependency_network": "GOPROXY=off GOSUMDB=off"}
    (root / "receipt.json").write_text(json.dumps(receipt,indent=2)+"\n")
    if any(r["exit_code"] for r in records):
        raise RuntimeError("Failed run retained")


def router_main():
    main = MAIN.replace('"github.com/gorilla/mux"', 'gofrHTTP "gofr.dev/pkg/gofr/http"')
    main = main.replace('"encoding/json";', '"encoding/json";"path/filepath";')
    main = main.replace('"options_matched"', '"static"')
    main = main.replace('[]string{"static","get_only"}', '[]string{"static","get_only","get_with_fallback"}')
    main = main.replace('r := mux.NewRouter()', 'r := gofrHTTP.NewRouter()')
    start = main.index('   route := r.HandleFunc(')
    end = main.index('   req.Header.Set(', start)
    main = main[:start] + r'''
   requestPath := "/example"
   if routeCase == "static" {
    if err := os.MkdirAll("public",0700); err != nil {panic(err)}
    if err := os.WriteFile("public/example",[]byte("fixture"),0600); err != nil {panic(err)}
    directory,err := filepath.Abs("public"); if err != nil {panic(err)}
    r.AddStaticFiles(logging.NewFileLogger(""),"/static",directory)
    requestPath = "/static/example"
   } else {
    r.Add("GET","/example",http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){w.WriteHeader(200)}))
    if routeCase == "get_with_fallback" {
     r.PathPrefix("/").Handler(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){w.WriteHeader(404)}))
    }
   }
   req := httptest.NewRequest(method,"http://example.test"+requestPath,nil)
''' + main[end:]
    return main


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("output")
    parser.add_argument("--actual-router", action="store_true")
    args = parser.parse_args()
    run(args.repo,args.output,args.actual_router)
