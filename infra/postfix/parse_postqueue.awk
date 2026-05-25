# Parse `postqueue -p` output into a JSON array of message objects.
# Usage: postqueue -p [queue] | awk -v status=active -f parse_postqueue.awk

BEGIN {
  print "["
  first = 1
}

function json_escape(str,    s) {
  s = str
  gsub(/\\/, "\\\\", s)
  gsub(/"/, "\\\"", s)
  return s
}

function flush_message(    i, to_json, arrival) {
  if (qid == "") {
    return
  }
  if (!first) {
    print ","
  }
  first = 0
  arrival = arrival_mon " " arrival_day " " arrival_time
  to_json = ""
  for (i = 1; i <= to_count; i++) {
    if (to_json != "") {
      to_json = to_json ","
    }
    to_json = to_json "\"" json_escape(to_list[i]) "\""
  }
  if (to_json == "") {
    to_json = "\"" json_escape(from_addr) "\""
  }
  printf "  {\"queue_id\":\"%s\",\"size_bytes\":%s,\"arrival\":\"%s\",\"from\":\"%s\",\"to\":[%s],\"status\":\"%s\"}",
    qid, size_bytes, json_escape(arrival), json_escape(from_addr), to_json, status
  reset_message()
}

function reset_message(    i) {
  qid = ""
  size_bytes = 0
  arrival_mon = ""
  arrival_day = ""
  arrival_time = ""
  from_addr = ""
  to_count = 0
  for (i in to_list) {
    delete to_list[i]
  }
}

/^[0-9A-F]+/ {
  flush_message()
  qid = $1
  size_bytes = $2 + 0
  arrival_mon = $3
  arrival_day = $4 " " $5
  arrival_time = $6
  from_addr = $7
  for (i = 8; i <= NF; i++) {
    from_addr = from_addr " " $i
  }
  to_count = 1
  to_list[1] = from_addr
  next
}

/^[[:space:]]/ {
  if (qid == "") {
    next
  }
  line = $0
  sub(/^[[:space:]]+/, "", line)
  if (line == "" || line ~ /^[(-]/) {
    next
  }
  to_count++
  to_list[to_count] = line
  next
}

END {
  flush_message()
  print ""
  print "]"
}
