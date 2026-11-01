from __future__ import annotations
import hashlib, json, os, shutil, subprocess, sys, time, signal
from pathlib import Path
from typing import Any
from .security import validate_changes

def code_version(root:Path, paths:tuple[str,...])->str:
    digest=hashlib.sha256()
    for rel in sorted(paths): digest.update(rel.encode()); digest.update((root/rel).read_bytes())
    return 'sha256:'+digest.hexdigest()[:16]
def copy_worktree(source:Path, destination:Path, paths:tuple[str,...])->None:
    destination.mkdir(parents=True,exist_ok=False)
    for rel in paths:
        src=source/rel; dst=destination/rel
        if src.is_dir(): shutil.copytree(src,dst)
        else: dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
def run_tests(work_root:Path, argv:list[str], timeout_seconds:int, log_dir:Path, call_id:str, expected_test_count:int|None=None)->dict[str,Any]:
    log_dir.mkdir(parents=True,exist_ok=True); start=time.monotonic(); timed_out=False
    stdout=''; stderr=''; exit_code=None; launch_error=False
    try:
        safe_env={k:v for k,v in os.environ.items() if k not in {'OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_MODEL','ANTHROPIC_API_KEY','GITHUB_TOKEN'}}
        safe_env.update({'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'})
        proc=subprocess.Popen(argv,cwd=work_root,env=safe_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0),start_new_session=os.name!='nt')
        try:
            stdout,stderr=proc.communicate(timeout=timeout_seconds); exit_code=proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out=True; stdout,stderr=exc.stdout or '',exc.stderr or ''
            if isinstance(stdout,bytes): stdout=stdout.decode('utf-8','replace')
            if isinstance(stderr,bytes): stderr=stderr.decode('utf-8','replace')
            if os.name=='nt':
                try: subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,text=True,timeout=5)
                except (OSError,subprocess.TimeoutExpired): pass
            else:
                try: os.killpg(proc.pid,signal.SIGKILL)
                except OSError: pass
            try: proc.kill()
            except OSError: pass
            try:
                tail_out,tail_err=proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                tail_out,tail_err=None,None
            # communicate() returns accumulated output, not an incremental tail.
            if tail_out is not None: stdout=tail_out
            if tail_err is not None: stderr=tail_err
            if isinstance(stdout,bytes): stdout=stdout.decode('utf-8','replace')
            if isinstance(stderr,bytes): stderr=stderr.decode('utf-8','replace')
            stderr+='\nTIMEOUT\n'
    except OSError as exc:
        launch_error=True; stderr=f'{type(exc).__name__}: {exc}\n'
    (log_dir/f'{call_id}.stdout.txt').write_text(stdout,encoding='utf-8'); (log_dir/f'{call_id}.stderr.txt').write_text(stderr,encoding='utf-8')
    merged=stdout+'\n'+stderr; lower=merged.lower();
    def count(word):
        import re
        found=re.findall(r'(\d+)\s+'+word+r'\b',merged); return int(found[-1]) if found else None
    if timed_out: status,verification,failure='timeout','inconclusive','timeout'
    elif launch_error or 'no tests ran' in lower or 'error collecting' in lower or 'modulenotfounderror' in lower: status,verification,failure='blocked','inconclusive','environment_error'
    elif exit_code==0 and (count('passed') or 0)>0 and (count('failed') or 0)==0 and (count('skipped') or 0)==0 and (expected_test_count is None or count('passed')==expected_test_count): status,verification,failure='ok','passed','passed'
    elif exit_code==0: status,verification,failure='blocked','inconclusive','not_reproduced'
    elif 'syntaxerror' in lower: status,verification,failure='failed','failed','syntax_error'
    else: status,verification,failure='failed','failed','assertion_failed' if 'assert' in lower or 'failed' in lower else 'code_runtime_error'
    return {'call_id':call_id,'tool':'run_tests','status':status,'verification_status':verification,'failure_class':failure,'exit_code':exit_code,'counts':{'passed':count('passed'),'failed':count('failed'),'skipped':count('skipped')},'stdout_path':f'{call_id}.stdout.txt','stderr_path':f'{call_id}.stderr.txt','duration_ms':round((time.monotonic()-start)*1000)}
def append_event(path:Path,event:dict[str,Any])->None:
    with path.open('a',encoding='utf-8') as f: f.write(json.dumps(event,ensure_ascii=False)+'\n')
