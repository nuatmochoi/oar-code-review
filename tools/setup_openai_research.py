#!/usr/bin/env python3
"""Read a key without echo; save outside the repository with mode 0600."""
import argparse,getpass,json,sys,urllib.request,urllib.error
from pathlib import Path
from research_openai_credentials import DEFAULT_PATH,save_credentials,load_credentials,headers

def check(path):
    credentials=load_credentials(path,environ={})
    request=urllib.request.Request('https://api.openai.com/v1/models/gpt-5.6-sol',headers=headers(credentials))
    try:
        with urllib.request.urlopen(request,timeout=30)as response:
            model=json.load(response)
    except urllib.error.HTTPError as error:
        # Authentication error bodies can include a portion of the key.
        print(f'모델 조회 실패: HTTP {error.code}. 키·프로젝트 권한을 확인하세요.',file=sys.stderr)
        return 1
    except (urllib.error.URLError,TimeoutError):
        print('API 연결 실패: 네트워크 또는 응답 시간을 확인하세요.',file=sys.stderr)
        return 1
    print('모델 조회 인증 성공: '+str(model.get('id','unknown')))
    print('이 검사는 텍스트 생성·잔액 조회를 수행하지 않습니다.')
    return 0

def main(argv=None):
    parser=argparse.ArgumentParser(description='논문 실험용 OpenAI API 키를 숨김 입력으로 저장합니다.')
    parser.add_argument('--path',type=Path,default=DEFAULT_PATH,help='로컬 키 설정 파일 경로')
    parser.add_argument('--project',help='선택: 크레딧이 있는 프로젝트 ID')
    parser.add_argument('--organization',help='선택: 크레딧이 있는 조직 ID')
    parser.add_argument('--replace',action='store_true',help='기존 키 설정을 교체')
    parser.add_argument('--check',action='store_true',help='저장된 키로 모델 조회 인증만 확인')
    args=parser.parse_args(argv)
    try:
        if args.check:return check(args.path)
        if not sys.stdin.isatty():
            print('터미널에서 직접 실행하세요. API 키는 화면에 표시되지 않습니다.',file=sys.stderr);return 2
        key=getpass.getpass('OpenAI API key (숨김 입력): ').strip()
        save_credentials(args.path,key,args.project,args.organization,replace=args.replace)
        print('저장 완료: '+str(args.path.expanduser()))
        print('파일 권한: 600. 키 값은 출력하지 않았습니다.')
        return 0
    except (EOFError,KeyboardInterrupt):
        print('\n입력을 취소했습니다.',file=sys.stderr);return 130
    except (OSError,ValueError)as error:
        # Our local validation messages contain no key material.
        if isinstance(error,FileNotFoundError):print('키 설정 파일이 없습니다. --check 없이 먼저 실행하세요.',file=sys.stderr)
        elif isinstance(error,PermissionError):print('키 설정 파일 접근 권한을 확인하세요.',file=sys.stderr)
        else:print(str(error),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
