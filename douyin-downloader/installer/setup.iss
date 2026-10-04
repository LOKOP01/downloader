; 视频下载器 — Inno Setup 安装脚本
; 编译：installer\build_setup.ps1 或 ISCC.exe setup.iss
; 产物：dist\视频下载器-<version>-setup.exe

#define MyAppName "视频下载器"
#define MyAppVersion "1.8.10.14"
#define MyAppPublisher "个人项目"
#define MyAppExeName "视频下载器.exe"

[Setup]
AppId={{8F3C2A91-6D4E-4B7A-9C1E-2F5D8A0B4E73}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright=仅供个人学习与备份使用
VersionInfoVersion=1.8.10.14
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion=1.8.10.14
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoCopyright=仅供个人学习与备份使用
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
OutputDir=..\dist
OutputBaseFilename=视频下载器-{#MyAppVersion}-setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=no
InfoBeforeFile=README.txt
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
AllowNoIcons=yes
ChangesAssociations=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: checkedonce

[Components]
Name: "main"; Description: "主程序"; Types: full compact custom; Flags: fixed
Name: "ffmpeg"; Description: "ffmpeg 合并组件（B 站 1080P+ 音视频合并，约 100MB）"; Types: full

[Types]
Name: "full"; Description: "完整安装（含 ffmpeg）"
Name: "compact"; Description: "精简安装（不含 ffmpeg，可稍后在设置里下载）"
Name: "custom"; Description: "自定义"; Flags: iscustom

[Files]
Source: "..\dist\{#MyAppVersion}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion; Components: main
Source: "..\dist\tools\ffmpeg.exe"; DestDir: "{app}\tools"; Flags: ignoreversion skipifsourcedoesntexist; Components: ffmpeg

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\tools"

; 运行日志是程序自己建的（{app}\logs），不删掉的话卸载后会留下空目录
Type: filesandordirs; Name: "{app}\logs"

[Code]
function EdgeExists(): Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{pf}\Microsoft\Edge\Application\msedge.exe')) or
    FileExists(ExpandConstant('{localappdata}\Microsoft\Edge\Application\msedge.exe')) or
    FileExists(ExpandConstant('{pf32}\Microsoft\Edge\Application\msedge.exe'));
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if WizardSilent then
    Exit;
  if not EdgeExists() then
  begin
    if MsgBox('未检测到 Microsoft Edge。' + #13#10 +
              '本程序解析抖音 / X / Instagram / 小红书等平台需要 Edge。' + #13#10 +
              '仍要继续安装吗？', mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    if UninstallSilent then
      Exit;
    if MsgBox('是否同时删除登录态和配置文件？' + #13#10 +
              '选择「是」将清除各平台登录信息和下载设置。',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      DelTree(ExpandConstant('{app}\edge_profile'), True, True, True);
      DelTree(ExpandConstant('{app}\edge_profile_ins'), True, True, True);
      DelTree(ExpandConstant('{app}\edge_profile_x'), True, True, True);
      DelTree(ExpandConstant('{app}\edge_profile_bili'), True, True, True);
      DelTree(ExpandConstant('{app}\edge_profile_xhs'), True, True, True);
      DelTree(ExpandConstant('{app}\edge_profile_jm'), True, True, True);
      DeleteFile(ExpandConstant('{app}\config.json'));
    end;
  end;
end;


