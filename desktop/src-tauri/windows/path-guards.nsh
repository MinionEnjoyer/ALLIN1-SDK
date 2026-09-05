; Fail closed before SDK destination writes. No PowerShell/Python dependency.
; These are preflight checks, not protection against concurrent same-user races.
!include LogicLib.nsh
!include FileFunc.nsh

!macro SDK_GUARD_INSTALL PREFIX
  Push "$INSTDIR"
  Call ${PREFIX}SDKGuardTree
  Push "$SMPROGRAMS\${PRODUCTNAME}.lnk"
  Call ${PREFIX}SDKGuardPath
  Push "$DESKTOP\${PRODUCTNAME}.lnk"
  Call ${PREFIX}SDKGuardPath
  !insertmacro SDK_GUARD_PAYLOAD "${PREFIX}"
!macroend

!macro SDK_GUARD_FUNCTIONS PREFIX
Function ${PREFIX}SDKUnsafePath
  !ifdef SDK_GUARD_DIAGNOSTICS
    FileOpen $9 "${SDK_GUARD_DIAGNOSTICS}" w
    FileWrite $9 "path=$0 r1=$1 r2=$2 r3=$3 r4=$4 r6=$6"
    FileClose $9
  !endif
  SetErrorLevel 87
  IfSilent +2
    MessageBox MB_OK|MB_ICONSTOP "The SDK destination contains an unsafe, redirected, inaccessible, or unsupported long path. Choose a local folder without links. No SDK payload was changed."
  Quit
FunctionEnd

; Input: absolute local path on stack. Preserve all caller registers.
; Reject normalization aliases before inspecting every existing ancestor.
Function ${PREFIX}SDKGuardPath
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  StrLen $1 $0
  ${If} $1 <= 3
  ${OrIf} $1 > 240
    Call ${PREFIX}SDKUnsafePath
  ${EndIf}
  StrCpy $1 $0 2 1
  ${If} $1 != ":\"
    Call ${PREFIX}SDKUnsafePath
  ${EndIf}
  StrCpy $1 $0 3
  System::Call 'kernel32::GetDriveTypeW(w r1)i.r2'
  ${If} $2 != 2
  ${AndIf} $2 != 3
    Call ${PREFIX}SDKUnsafePath
  ${EndIf}
  System::Call 'kernel32::GetFullPathNameW(w r0, i ${NSIS_MAX_STRLEN}, w .r1, p 0)i.r2'
  ${If} $2 = 0
  ${OrIf} $2 >= ${NSIS_MAX_STRLEN}
  ${OrIf} $1 != $0
    Call ${PREFIX}SDKUnsafePath
  ${EndIf}
  StrCpy $2 3
  StrCpy $3 ""
  scan_chars:
    StrCpy $1 $0 1 $2
    ${If} $1 == ""
    ${OrIf} $1 == "\"
      ${If} $3 == ""
      ${OrIf} $3 == "."
      ${OrIf} $3 == " "
        Call ${PREFIX}SDKUnsafePath
      ${EndIf}
    ${EndIf}
    StrCmp $1 "" scan_ancestors
    ${If} $1 == "/"
    ${OrIf} $1 == ":"
    ${OrIf} $1 == "*"
    ${OrIf} $1 == "?"
    ${OrIf} $1 == "$\""
    ${OrIf} $1 == "<"
    ${OrIf} $1 == ">"
    ${OrIf} $1 == "|"
    ${OrIf} $1 == "$\r"
    ${OrIf} $1 == "$\n"
    ${OrIf} $1 == "$\t"
      Call ${PREFIX}SDKUnsafePath
    ${EndIf}
    StrCpy $3 $1
    ${If} $1 == "\"
      StrCpy $3 ""
    ${EndIf}
    IntOp $2 $2 + 1
    Goto scan_chars
  scan_ancestors:
    System::Call 'kernel32::GetFileAttributesW(w r0)i.r1 ?e'
    Pop $2
    ${If} $1 = -1
      ${If} $2 != 2
      ${AndIf} $2 != 3
        Call ${PREFIX}SDKUnsafePath
      ${EndIf}
    ${Else}
      IntOp $2 $1 & 0x400
      ${If} $2 != 0
        Call ${PREFIX}SDKUnsafePath
      ${EndIf}
      IntOp $2 $1 & 0x10
      ${If} $2 = 0
        ; A hard-linked destination can overwrite bytes outside the install root.
        System::Call 'kernel32::CreateFileW(w r0, i 0, i 7, p 0, i 3, i 0x200000, p 0)p.r4'
        ${If} $4 = -1
          Call ${PREFIX}SDKUnsafePath
        ${EndIf}
        System::Alloc 52
        Pop $5
        System::Call 'kernel32::GetFileInformationByHandle(p r4, p r5)i.r6'
        System::Call 'kernel32::CloseHandle(p r4)'
        ${If} $6 = 0
          System::Free $5
          Call ${PREFIX}SDKUnsafePath
        ${EndIf}
        ; BY_HANDLE_FILE_INFORMATION.nNumberOfLinks is the DWORD at offset 40.
        System::Call '*$5(i, i, i, i, i, i, i, i, i, i, i.r6)'
        System::Free $5
        ${If} $6 != 1
          Call ${PREFIX}SDKUnsafePath
        ${EndIf}
      ${EndIf}
    ${EndIf}
    StrLen $2 $0
    ${If} $2 > 3
      ${GetParent} $0 $1
      ${If} $1 == $0
        Call ${PREFIX}SDKUnsafePath
      ${EndIf}
      StrLen $2 $1
      ${If} $2 = 2
        StrCpy $1 "$1\"
      ${EndIf}
      StrCpy $0 $1
      Goto scan_ancestors
    ${EndIf}
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Preflight the complete existing destination tree without following links.
Function ${PREFIX}SDKGuardTree
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $0
  Call ${PREFIX}SDKGuardPath
  System::Call 'kernel32::GetFileAttributesW(w r0)i.r1'
  StrCmp $1 -1 done_tree
  IntOp $2 $1 & 0x10
  ${If} $2 = 0
    Call ${PREFIX}SDKUnsafePath
  ${EndIf}
  System::Alloc 592
  Pop $5
  System::Call 'kernel32::FindFirstFileW(w "$0\*", p r5)p.r1 ?e'
  Pop $4
  StrCmp $1 -1 tree_enumeration_end
  next_entry:
    IntOp $3 $5 + 44
    System::Call '*$3(&w260.r2)'
    ${If} $2 != "."
    ${AndIf} $2 != ".."
      StrCpy $3 "$0\$2"
      Push $3
      Call ${PREFIX}SDKGuardPath
      System::Call 'kernel32::GetFileAttributesW(w r3)i.r4'
      IntOp $4 $4 & 0x10
      ${If} $4 != 0
        Push $3
        Call ${PREFIX}SDKGuardTree
      ${EndIf}
    ${EndIf}
    System::Call 'kernel32::FindNextFileW(p r1, p r5)i.r3 ?e'
    Pop $4
    StrCmp $3 0 close_tree next_entry
  close_tree:
    System::Call 'kernel32::FindClose(p r1)'
    System::Free $5
    ${If} $4 != 18
      Call ${PREFIX}SDKUnsafePath
    ${EndIf}
    Goto done_tree
  tree_enumeration_end:
    System::Free $5
    ${If} $4 != 2
    ${AndIf} $4 != 18
      Call ${PREFIX}SDKUnsafePath
    ${EndIf}
  done_tree:
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd
!macroend

!insertmacro SDK_GUARD_FUNCTIONS ""
!insertmacro SDK_GUARD_FUNCTIONS "un."
