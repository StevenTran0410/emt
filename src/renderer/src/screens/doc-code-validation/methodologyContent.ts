export interface MethodologyDimension {
  id: string
  dimension: string
  keyQuestion: string
  howItWorks: string
  cobolCheck: string[]
  jclCheck: string[]
  solution: string
  passCriteria: string
}

export const METHODOLOGY_COMPLETENESS: MethodologyDimension[] = [
  {
    id: 'entity-program',
    dimension: 'Program Entity Coverage',
    keyQuestion: 'Are all COBOL programs specified in the BD/DD present in the implementation source code, and vice versa?',
    howItWorks: 'Extracts PROGRAM-ID declarations from source code and compares against program nodes defined in the document graph.',
    cobolCheck: [
      'COBOL PROGRAM-ID headers in CBL/COB files',
      'Sub-program PROCEDURE DIVISION ENTRY points'
    ],
    jclCheck: [
      'EXEC PGM= program call references in JCL steps',
      'JCL procedure EXEC PROC= invocations'
    ],
    solution: 'Add missing program documentation to BD/DD, or create source skeletons for undocumented programs.',
    passCriteria: '100% match between documented programs and existing source PROGRAM-IDs.'
  },
  {
    id: 'entity-job',
    dimension: 'JCL Job Entity Coverage',
    keyQuestion: 'Does the document graph capture all JCL batch jobs present in the codebase?',
    howItWorks: 'Scans //JOB cards across all JCL files and aligns with job entities in the cluster.',
    cobolCheck: [
      'Main drivers invoked by batch job steps'
    ],
    jclCheck: [
      '//JOB statements in JCL files',
      'JOB card parameter definitions'
    ],
    solution: 'Register missing batch job definitions in BD design documents.',
    passCriteria: 'All active JCL batch jobs are registered in document graph.'
  },
  {
    id: 'entity-dataset',
    dimension: 'Dataset & DD Entity Coverage',
    keyQuestion: 'Are all DD statements and physical VSAM/sequential datasets documented?',
    howItWorks: 'Parses DD DSN= cards and SELECT...ASSIGN COBOL file controls against documented dataset/dd entities.',
    cobolCheck: [
      'FILE-CONTROL SELECT...ASSIGN clauses',
      'FD file descriptor data names'
    ],
    jclCheck: [
      '//DD DSN= dataset allocations',
      'Temporary &&DSN and SYSOUT allocations'
    ],
    solution: 'Document dataset layout specifications and logical DD names.',
    passCriteria: 'Zero undocumented physical datasets accessed by production jobs.'
  }
]

export const METHODOLOGY_CORRECTNESS: MethodologyDimension[] = [
  {
    id: 'relation-calls',
    dimension: 'Program Call Relations (calls)',
    keyQuestion: 'Do documented sub-program CALL chains accurately reflect the actual CALL statements in COBOL?',
    howItWorks: 'Checks static CALL "LITERAL" facts and dynamic CALL identifier facts against documented program-to-program relations.',
    cobolCheck: [
      'CALL "PROGNAME" USING parameters',
      'CICS LINK / XCTL PROGRAM("PROGNAME")'
    ],
    jclCheck: [
      'Step-to-program execution chains'
    ],
    solution: 'Update DD sequence diagrams and call tree tables to match exact CALL targets.',
    passCriteria: 'All documented CALL edges exist in source code with exact target match.'
  },
  {
    id: 'relation-copies',
    dimension: 'Copybook Inclusions (copies)',
    keyQuestion: 'Are copybook dependencies (COPY / EXEC SQL INCLUDE) accurately documented for each program?',
    howItWorks: 'Cross-references COPY statements and SQL INCLUDE copybooks against documented copybook associations.',
    cobolCheck: [
      'COPY "MEMBER" REPLACING statements',
      'EXEC SQL INCLUDE DCLGEN END-EXEC'
    ],
    jclCheck: [
      'JCLLIB ORDER= procedure library inclusions'
    ],
    solution: 'Ensure DCLGENs and shared record copybooks are correctly linked to programs.',
    passCriteria: 'No missing COPY relationships in critical business logic programs.'
  },
  {
    id: 'relation-binds',
    dimension: 'Dataset Binding (binds_dd)',
    keyQuestion: 'Are DD allocations in JCL steps bound to the correct COBOL internal files?',
    howItWorks: 'Matches JCL step DD names with COBOL ASSIGN TO external DD names.',
    cobolCheck: [
      'SELECT FILE ASSIGN TO "DDNAME"'
    ],
    jclCheck: [
      '//STEP.DDNAME DD DSN=DATASET.NAME'
    ],
    solution: 'Align DD names between JCL job steps and COBOL FILE-CONTROL clauses.',
    passCriteria: 'Every job step DD correctly binds to its corresponding COBOL file.'
  }
]
