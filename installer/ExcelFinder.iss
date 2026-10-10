; Excel Finder: Windows installer (Inno Setup 6.3+).  Build:  iscc installer\ExcelFinder.iss /DAppVersion=1.0.0
; Roz ka tareeka: installer\build_windows.ps1 (ye khud PyInstaller chalakar phir ise compile karta hai).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppPublisher
  #define AppPublisher "Excel Finder"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\ExcelFinder"
#endif
#define AppName "Excel Finder"
#define AppExe "ExcelFinder.exe"

[Setup]
; AppId kabhi mat badalna: wahi ID ho toh naya installer purane ko update karta hai (data bacha rehta hai)
AppId={{B7D9A6F4-3C2E-4E0A-9B52-6B1F0E7A1C11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=ExcelFinder-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Admin rights ki zarurat nahi: customer sirf apne user ke liye install kar sakta hai (dialog me sabke liye bhi chun sakta hai)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=yes
RestartApplications=no
#if FileExists("EULA.txt")
; installer\EULA.txt rakh do toh install se pehle customer ko dikhegi (license agreement)
LicenseFile=EULA.txt
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
; In-app "Update now" silent install chalata hai: tab app khud wapas khul jata hai
Filename: "{app}\{#AppExe}"; Flags: nowait skipifnotsilent

[UninstallRun]
; Chalta hua app band karo, warna uninstall uski files delete nahi kar paata
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopExcelFinder"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
  begin
    { Data (index, settings, license) customer ke apne folder me hota hai: poochkar hi hatao }
    if MsgBox('Do you also want to delete your Excel Finder data (search index, settings and license)?' + #13#10 + #13#10 +
              'Choose No to keep it, so a future reinstall continues where you left off.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{localappdata}\ExcelFinder'), True, True, True);
  end;
end;
