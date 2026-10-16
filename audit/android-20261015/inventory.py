import pathlib,json,hashlib,collections
root=pathlib.Path('android')
rows=[]
for p in sorted(root.rglob('*')):
 if not p.is_file() or any(part in ('build','.gradle','.kotlin') for part in p.parts):continue
 if p.name=='local.properties':continue
 content=p.read_bytes()
 rows.append({'path':p.as_posix(),'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),'kind':'binary' if p.suffix in ('.jar','.png','.webp') else 'text','audit_status':'pending'})
pathlib.Path('audit/android-20261015/inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print('PROJECT_FILES',len(rows))
print('MAIN_KOTLIN',sum(p['path'].startswith('android/app/src/main/') and p['path'].endswith('.kt') for p in rows))
print('TEST_KOTLIN',sum(p['path'].startswith('android/app/src/test/') and p['path'].endswith('.kt') for p in rows))
print('JAVA',sum(p['path'].endswith('.java') for p in rows))
