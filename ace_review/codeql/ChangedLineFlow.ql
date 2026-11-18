/**
 * @name Changed-line evidence flow
 * @description Tracks values originating on the selected changed line to review-relevant sinks.
 * @kind path-problem
 * @problem.severity recommendation
 * @id ace-review/changed-line-flow
 * @precision medium
 */

import go
import semmle.go.dataflow.DataFlow

module Config implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    source.getLocation().getFile().getRelativePath() = "api/types.go" and
    source.getLocation().getStartLine() = 614
  }

  predicate isSink(DataFlow::Node sink) {
    sink instanceof DataFlow::TypeCastNode
    or
    exists(DataFlow::CallNode call | sink = call.getAnArgument())
    or
    sink = DataFlow::exprNode(any(ReturnStmt ret).getAnExpr())
  }
}

module Flow = DataFlow::Global<Config>;

import Flow::PathGraph

from Flow::PathNode source, Flow::PathNode sink
where Flow::flowPath(source, sink) and source != sink
select sink, source, sink,
  "A value from changed code flows from $@ to the review-relevant sink $@.", source,
  source.getNode().toString(), sink, sink.getNode().toString()
