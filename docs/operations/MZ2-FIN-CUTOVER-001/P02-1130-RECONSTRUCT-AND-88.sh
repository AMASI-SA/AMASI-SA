#!/usr/bin/env bash
echo 'RESULT=DEPRECATED_SUP_20260922_27'
echo 'DETAIL=Do not run this historical helper. Its raw Git index byte-SHA guard is stricter than semantic index integrity and caused a false blocker after exact worktree reconstruction.'
echo 'COMMIT=NONE'
echo 'PUSH=NONE'
echo 'P02=LOCKED'
exit 2
