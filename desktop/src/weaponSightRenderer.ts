import { cross, sub, viewProjection, type Rig, type SightMesh, type Vec } from "./weaponSightMath";

export function triangleNormal(triangle: Vec[]): Vec|null {
  const raw=cross(sub(triangle[1],triangle[0]),sub(triangle[2],triangle[0])),length=Math.hypot(...raw);
  if(!Number.isFinite(length))throw new Error("Nonfinite sight triangle");
  // Tiny but valid triangles are common in authored sights. The camera's
  // direction threshold is not a valid threshold for a triangle's area.
  return length===0?null:raw.map(v=>v/length) as Vec;
}

/** Depth-buffered, double-sided diagnostic geometry; not a game-material renderer. */
export function createSightRenderer(canvas: HTMLCanvasElement) {
  const gl=canvas.getContext("webgl",{alpha:false,antialias:true,preserveDrawingBuffer:true});
  if(!gl) throw new Error("WebGL is unavailable; sight geometry cannot be displayed");
  const shader=(type:number,source:string)=>{
    const s=gl.createShader(type)!;gl.shaderSource(s,source);gl.compileShader(s);
    if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)) {const message=gl.getShaderInfoLog(s);gl.deleteShader(s);throw new Error(message??"Sight shader failed");}
    return s;
  };
  const vs=shader(gl.VERTEX_SHADER,"attribute vec3 position; attribute vec3 normal; uniform mat4 matrix; varying float shade; void main(){ gl_Position=matrix*vec4(position,1.); shade=.28+.72*abs(dot(normalize(normal),normalize(vec3(-.3,-.6,1.)))); }");
  const fs=shader(gl.FRAGMENT_SHADER,"precision mediump float; varying float shade; uniform vec3 tint; void main(){ gl_FragColor=vec4(tint*shade,1.); }");
  const program=gl.createProgram()!;gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);
  gl.deleteShader(vs);gl.deleteShader(fs);
  if(!gl.getProgramParameter(program,gl.LINK_STATUS)) {gl.deleteProgram(program);throw new Error("Sight shader link failed");}
  const buffer=gl.createBuffer()!,position=gl.getAttribLocation(program,"position"),normal=gl.getAttribLocation(program,"normal");
  let count=0;
  return {
    upload(meshes: SightMesh[]) {
      const data:number[]=[];
      for(const mesh of meshes) for(let i=0;i<mesh.triangles.length;i+=3) {
        const triangle=mesh.triangles.slice(i,i+3).map(k=>mesh.positions.slice(k*3,k*3+3) as Vec);
        const n=triangleNormal(triangle);
        if(!n)continue;
        for(const point of triangle) data.push(...point,...n);
      }
      count=data.length/6;gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(data),gl.DYNAMIC_DRAW);
    },
    draw(rig: Rig) {
      const matrix=viewProjection(rig,canvas.width/canvas.height);
      gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(.075,.10,.125,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);gl.disable(gl.CULL_FACE);gl.useProgram(program);gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
      gl.enableVertexAttribArray(position);gl.vertexAttribPointer(position,3,gl.FLOAT,false,24,0);
      gl.enableVertexAttribArray(normal);gl.vertexAttribPointer(normal,3,gl.FLOAT,false,24,12);
      gl.uniformMatrix4fv(gl.getUniformLocation(program,"matrix"),false,matrix);gl.uniform3f(gl.getUniformLocation(program,"tint"),.68,.74,.78);
      gl.drawArrays(gl.TRIANGLES,0,count);
    },
    dispose() {gl.deleteBuffer(buffer);gl.deleteProgram(program);},
  };
}
