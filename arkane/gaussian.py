#!/usr/bin/env python
# -*- coding: utf-8 -*-

###############################################################################
#                                                                             #
# RMG - Reaction Mechanism Generator                                          #
#                                                                             #
# Copyright (c) 2002-2019 Prof. William H. Green (whgreen@mit.edu),           #
# Prof. Richard H. West (r.west@neu.edu) and the RMG Team (rmg_dev@mit.edu)   #
#                                                                             #
# Permission is hereby granted, free of charge, to any person obtaining a     #
# copy of this software and associated documentation files (the 'Software'),  #
# to deal in the Software without restriction, including without limitation   #
# the rights to use, copy, modify, merge, publish, distribute, sublicense,    #
# and/or sell copies of the Software, and to permit persons to whom the       #
# Software is furnished to do so, subject to the following conditions:        #
#                                                                             #
# The above copyright notice and this permission notice shall be included in  #
# all copies or substantial portions of the Software.                         #
#                                                                             #
# THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND, EXPRESS OR  #
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,    #
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE #
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER      #
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING     #
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER         #
# DEALINGS IN THE SOFTWARE.                                                   #
#                                                                             #
###############################################################################

import math
import numpy
import logging
import os.path

import rmgpy.constants as constants
from rmgpy.statmech import IdealGasTranslation, NonlinearRotor, LinearRotor, HarmonicOscillator, Conformer
from rmgpy.exceptions import InputError

from arkane.common import check_conformer_energy, get_element_mass
from arkane.log import Log

################################################################################


class GaussianLog(Log):
    """
    Represent a log file from Gaussian. The attribute `path` refers to the
    location on disk of the Gaussian log file of interest. Methods are provided
    to extract a variety of information into Arkane classes and/or NumPy
    arrays.
    """

    def __init__(self, path):
        self.path = path

    def getNumberOfAtoms(self):
        """
        Return the number of atoms in the molecular configuration used in
        the Gaussian log file.
        """

        Natoms = 0
        # Open Gaussian log file for parsing
        f = open(self.path, 'r')
        line = f.readline()
        while line != '' and Natoms == 0:
            # Automatically determine the number of atoms
            if 'Input orientation:' in line and Natoms == 0:
                for i in range(5): line = f.readline()
                while '---------------------------------------------------------------------' not in line:
                    Natoms += 1
                    line = f.readline()
            line = f.readline()
        # Close file when finished
        f.close()
        # Return the result
        return Natoms

    def loadForceConstantMatrix(self):
        """
        Return the force constant matrix from the Gaussian log file. The job
        that generated the log file must have the option ``iop(7/33=1)`` in
        order for the proper force constant matrix (in Cartesian coordinates)
        to be printed in the log file. If multiple such matrices are identified,
        only the last is returned. The units of the returned force constants
        are J/m^2. If no force constant matrix can be found in the log file,
        ``None`` is returned.
        """

        F = None

        Natoms = self.getNumberOfAtoms()
        Nrows = Natoms * 3

        f = open(self.path, 'r')
        line = f.readline()
        while line != '':
            # Read force constant matrix
            if 'Force constants in Cartesian coordinates:' in line:
                F = numpy.zeros((Nrows,Nrows), numpy.float64)
                for i in range(int(math.ceil(Nrows / 5.0))):
                    # Header row
                    line = f.readline()
                    # Matrix element rows
                    for j in range(i*5, Nrows):
                        data = f.readline().split()
                        for k in range(len(data)-1):
                            F[j,i*5+k] = float(data[k+1].replace('D', 'E'))
                            F[i*5+k,j] = F[j,i*5+k]
                # Convert from atomic units (Hartree/Bohr_radius^2) to J/m^2
                F *= 4.35974417e-18 / 5.291772108e-11**2
            line = f.readline()
        # Close file when finished
        f.close()

        return F

    def loadGeometry(self):
        """
        Return the optimum geometry of the molecular configuration from the
        Gaussian log file. If multiple such geometries are identified, only the
        last is returned.
        """

        number, coord, mass = [], [], []

        f = open(self.path, 'r')
        line = f.readline()
        while line != '':
            # Automatically determine the number of atoms
            if 'Input orientation:' in line:
                number, coord = [], []
                for i in range(5): line = f.readline()
                while '---------------------------------------------------------------------' not in line:
                    data = line.split()
                    number.append(int(data[1]))
                    coord.append([float(data[3]), float(data[4]), float(data[5])])
                    line = f.readline()
            line = f.readline()
        # Close file when finished
        f.close()

        # Assign appropriate mass to each atom in the molecule
        mass = []
        for num in number:
            mass1, _ = get_element_mass(num)
            mass.append(mass1)
        coord = numpy.array(coord, numpy.float64)
        number = numpy.array(number, numpy.int)
        mass = numpy.array(mass, numpy.float64)
        if len(number) == 0 or len(coord) == 0 or len(mass) == 0:
            raise InputError('Unable to read atoms from Gaussian geometry output file {0}. '
                             'Make sure the output file is not corrupt.\nNote: if your species has '
                             '50 or more atoms, you will need to add the `iop(2/9=2000)` keyword to your '
                             'input file so Gaussian will print the input orientation geomerty.'.format(self.path))
        
        return coord, number, mass

    def loadConformer(self, symmetry=None, spinMultiplicity=0, opticalIsomers=None, symfromlog=None, label=''):
        """
        Load the molecular degree of freedom data from a log file created as
        the result of a Gaussian "Freq" quantum chemistry calculation. As
        Gaussian's guess of the external symmetry number is not always correct,
        you can use the `symmetry` parameter to substitute your own value; if
        not provided, the value in the Gaussian log file will be adopted. In a
        log file with multiple Thermochemistry sections, only the last one will
        be kept.
        """

        modes = []
        unscaled_frequencies = []
        E0 = 0.0

        f = open(self.path, 'r')
        line = f.readline()
        while line != '':

            # Read the spin multiplicity if not explicitly given
            if spinMultiplicity == 0 and 'Multiplicity =' in line:
                spinMultiplicity = int(line.split()[-1])
                logging.debug('Conformer {0} is assigned a spin multiplicity of {1}'.format(label, spinMultiplicity))

            # The data we want is in the Thermochemistry section of the output
            if '- Thermochemistry -' in line:
                modes = []
                inPartitionFunctions = False
                line = f.readline()
                while line != '':

                    # This marks the end of the thermochemistry section
                    if '-------------------------------------------------------------------' in line:
                        break

                    # Read molecular mass for external translational modes
                    elif 'Molecular mass:' in line:
                        mass = float(line.split()[2])
                        translation = IdealGasTranslation(mass=(mass,"amu"))
                        modes.append(translation)

                    # Read Gaussian's estimate of the external symmetry number
                    elif 'Rotational symmetry number' in line and symmetry is None:
                        if symfromlog is True:
                            symmetry = int(float(line.split()[3]))

                    # Read moments of inertia for external rotational modes
                    elif 'Rotational constants (GHZ):' in line:
                        inertia = [float(d) for d in line.split()[-3:]]
                        for i in range(3):
                            inertia[i] = constants.h / (8 * constants.pi * constants.pi * inertia[i] * 1e9) *constants.Na*1e23
                        rotation = NonlinearRotor(inertia=(inertia,"amu*angstrom^2"), symmetry=symmetry)
                        modes.append(rotation)
                    elif 'Rotational constant (GHZ):' in line:
                        inertia = [float(line.split()[3])]
                        inertia[0] = constants.h / (8 * constants.pi * constants.pi * inertia[0] * 1e9) *constants.Na*1e23
                        rotation = LinearRotor(inertia=(inertia[0],"amu*angstrom^2"), symmetry=symmetry)
                        modes.append(rotation)

                    # Read vibrational modes
                    elif 'Vibrational temperatures:' in line:
                        frequencies = []
                        frequencies.extend([float(d) for d in line.split()[2:]])
                        line = f.readline()
                        frequencies.extend([float(d) for d in line.split()[1:]])
                        line = f.readline()
                        while line.strip() != '':
                            frequencies.extend([float(d) for d in line.split()])
                            line = f.readline()
                        # Convert from K to cm^-1
                        if len(frequencies) > 0:
                            frequencies = [freq * 0.695039 for freq in frequencies]  # kB = 0.695039 cm^-1/K
                            unscaled_frequencies = frequencies
                            vibration = HarmonicOscillator(frequencies=(frequencies,"cm^-1"))
                            modes.append(vibration)

                    # Read ground-state energy
                    elif 'Sum of electronic and zero-point Energies=' in line:
                        E0 = float(line.split()[6]) * 4.35974394e-18 * constants.Na

                    # Read spin multiplicity if above method was unsuccessful
                    elif 'Electronic' in line and inPartitionFunctions and spinMultiplicity == 0:
                        spinMultiplicity = int(float(line.split()[1].replace('D', 'E')))

                    elif 'Log10(Q)' in line:
                        inPartitionFunctions = True

                    # Read the next line in the file
                    line = f.readline()

            # Read the next line in the file
            line = f.readline()

        # Close file when finished
        f.close()
        if opticalIsomers is None:
            opticalIsomers = self.get_optical_isomers_and_symmetry_number()[0]
        return Conformer(E0=(E0*0.001,"kJ/mol"), modes=modes, spinMultiplicity=spinMultiplicity,
                         opticalIsomers=opticalIsomers), unscaled_frequencies

    def loadEnergy(self,frequencyScaleFactor=1.):
        """
        Load the energy in J/mol from a Gaussian log file. The file is checked 
        for a complete basis set extrapolation; if found, that value is 
        returned. Only the last energy in the file is returned. The zero-point
        energy is *not* included in the returned value; it is removed from the
        CBS-QB3 value.
        """

        E0 = None; E0_cbs = None; scaledZPE = None

        f = open(self.path, 'r')
        line = f.readline()
        while line != '':

            if 'SCF Done:' in line:
                E0 = float(line.split()[4]) * constants.E_h * constants.Na
            elif 'CBS-QB3 (0 K)' in line:
                E0_cbs = float(line.split()[3]) * constants.E_h * constants.Na
            elif 'G3(0 K)' in line:
                E0_cbs = float(line.split()[2]) * constants.E_h * constants.Na
            
            # Read the ZPE from the "E(ZPE)=" line, as this is the scaled version.
            # Gaussian defines the following as
            # E (0 K) = Elec + E(ZPE), 
            # The ZPE is the scaled ZPE given by E(ZPE) in the log file, 
            # hence to get the correct Elec from E (0 K) we need to subtract the scaled ZPE
            
            elif 'E(ZPE)' in line:
                scaledZPE = float(line.split()[1]) * constants.E_h * constants.Na
            elif '\\ZeroPoint=' in line:
                line = line.strip() + f.readline().strip()
                start = line.find('\\ZeroPoint=') + 11
                end = line.find('\\', start)
                scaledZPE = float(line[start:end]) * constants.E_h * constants.Na * frequencyScaleFactor
            # Read the next line in the file
            line = f.readline()

        # Close file when finished
        f.close()
        
        if E0_cbs is not None:
            if scaledZPE is None:
                raise Exception('Unable to find zero-point energy in Gaussian log file.')
            return E0_cbs - scaledZPE
        elif E0 is not None:
            return E0
        else: raise Exception('Unable to find energy in Gaussian log file.')
    
    def loadZeroPointEnergy(self):
        """
        Load the unscaled zero-point energy in J/mol from a Gaussian log file.
        """

        ZPE = None

        f = open(self.path, 'r')
        line = f.readline()
        while line != '':

            # Do NOT read the ZPE from the "E(ZPE)=" line, as this is the scaled version!
            # We will read in the unscaled ZPE and later multiply the scaling factor
            # from the input file
            if 'Zero-point correction=' in line:
                ZPE = float(line.split()[2]) * constants.E_h * constants.Na
            elif '\\ZeroPoint=' in line:
                line = line.strip() + f.readline().strip()
                start = line.find('\\ZeroPoint=') + 11
                end = line.find('\\', start)
                ZPE = float(line[start:end]) * constants.E_h * constants.Na
            # Read the next line in the file
            line = f.readline()

        # Close file when finished
        f.close()
        
        if ZPE is not None:
            return ZPE
        else:
            raise Exception('Unable to find zero-point energy in Gaussian log file.')

    def loadScanEnergies(self):
        """
        Extract the optimized energies in J/mol from a log file, e.g. the 
        result of a Gaussian "Scan" quantum chemistry calculation.
        """

        optfreq = False
        rigidScan=False

        # The array of potentials at each scan angle
        Vlist = []

        # Parse the Gaussian log file, extracting the energies of each
        # optimized conformer in the scan
        f = open(self.path, 'r')
        line = f.readline()
        while line != '':
            # If the job contains a "freq" then we want to ignore the last energy
            if ' freq ' in line:
                optfreq = True
            #if # scan is keyword instead of # opt, then this is a rigid scan job
            #and parsing the energies is done a little differently
            if '# scan' in line:
                rigidScan=True
            # The lines containing "SCF Done" give the energy at each
            # iteration (even the intermediate ones)
            if 'SCF Done:' in line:
                E = float(line.split()[4])
                #rigid scans will only not optimize, so just append every time it finds an energy.
                if rigidScan:
                    Vlist.append(E)
            # We want to keep the values of E that come most recently before
            # the line containing "Optimization completed", since it refers
            # to the optimized geometry
            if 'Optimization completed' in line:
                Vlist.append(E)
            line = f.readline()
        # Close file when finished
        f.close()
        
        #give warning in case this assumption is not true
        if rigidScan==True:
            print '   Assuming', os.path.basename(self.path), 'is the output from a rigid scan...'
        
        Vlist = numpy.array(Vlist, numpy.float64)
        # check to see if the scanlog indicates that a one of your reacting species may not be the lowest energy conformer
        check_conformer_energy(Vlist, self.path)
        
        # Adjust energies to be relative to minimum energy conformer
        # Also convert units from Hartree/particle to J/mol
        Vlist -= numpy.min(Vlist)
        Vlist *= constants.E_h * constants.Na

        if optfreq: Vlist = Vlist[:-1]

        # Determine the set of dihedral angles corresponding to the loaded energies
        # This assumes that you start at 0.0, finish at 360.0, and take
        # constant step sizes in between
        angle = numpy.arange(0.0, 2*math.pi+0.00001, 2*math.pi/(len(Vlist)-1), numpy.float64)

        return Vlist, angle

    def loadNegativeFrequency(self):
        """
        Return the negative frequency from a transition state frequency
        calculation in cm^-1.
        """
        frequency = None
        frequencies = []
        f = open(self.path, 'r')
        line = f.readline()
        while line != '':
            # Read vibrational frequencies
            if 'Frequencies --' in line:
                frequencies.extend(line.split()[2:])
            line = f.readline()
        # Close file when finished
        f.close()
        
        frequencies = [float(freq) for freq in frequencies]
        frequencies.sort()
        frequency = [freq for freq in frequencies if freq < 0][0]
        if frequency is None:
            raise Exception('Unable to find imaginary frequency in Gaussian output file {0}'.format(self.path))
        return frequency
