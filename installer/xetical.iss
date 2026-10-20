; Xetical 설치 프로그램(Inno Setup 6). scripts\build_exe.py가 dist\Xetical\을 만든 뒤 이 파일로 묶는다.
; 직접: ISCC.exe /DMyAppVersion=1.2.0 installer\xetical.iss  →  dist\Xetical-1.2.0-setup.exe
;
; - 기본은 관리자 권한 없이 이 사용자에게만 설치(%LOCALAPPDATA%\Programs\Xetical). 첫 화면에서 모든 사용자로 바꿀 수 있다.
; - 설치본에는 portable.txt를 넣지 않는다 → 설정·로그인 정보는 %APPDATA%\Xetical에 저장된다.
; - 제거할 때 그 데이터를 지울지 묻고, 'Windows 시작 시 실행' 등록도 지운다.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{1C6C7623-B6F5-429E-81A5-0F2D4C782AB1}
AppName=Xetical
AppVersion={#MyAppVersion}
AppVerName=Xetical {#MyAppVersion}
AppPublisher=0eti
AppPublisherURL=https://github.com/0eti/Xetical
AppSupportURL=https://github.com/0eti/Xetical/issues
AppUpdatesURL=https://github.com/0eti/Xetical/releases
DefaultDirName={autopf}\Xetical
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Xetical-{#MyAppVersion}-setup
SetupIconFile=..\assets\xetical.ico
UninstallDisplayIcon={app}\Xetical.exe
UninstallDisplayName=Xetical
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Xetical\*"; DestDir: "{app}"; Excludes: "portable.txt"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Xetical"; Filename: "{app}\Xetical.exe"
Name: "{autodesktop}\Xetical"; Filename: "{app}\Xetical.exe"; Tasks: desktopicon

[Registry]
; 앱이 'Windows 시작 시 실행'으로 만든 값을 제거할 때 지운다(설치 때는 만들지 않는다)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Xetical"; ValueType: none; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\Xetical.exe"; Description: "{cm:LaunchProgram,Xetical}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Dir := ExpandConstant('{userappdata}\Xetical');
    if DirExists(Dir) and (not UninstallSilent) then
      if MsgBox('설정과 로그인 정보도 지울까요?' + #13#10 + Dir + #13#10#13#10 +
                '다시 설치해서 쓸 거라면 [아니요]를 누르세요.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Dir, True, True, True);
  end;
end;
