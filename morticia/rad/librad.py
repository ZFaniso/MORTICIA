__author__ = 'DGriffith'
"""
 *--------------------------------------------------------------------
 *
 * This file is part of MORTICIA.
 * Copyright (c) 2015-2016 by Derek Griffith and Ari Ramkiloawn
 *
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 *--------------------------------------------------------------------"""

"""
.. module:: librad
    :platform: Unix only if running of libRadtran/uvspec is required. Also Windows for setting up uvspec cases.
    :synopsis: This module provides access to libRadtran through the uvspec utility. For information on libRadtran go to
    http://www.libtradtran.org
Some of the code within this module and code imported by the module is provided with libRadtran in the src_py
directory. In order to run libRadtran cases it is necessary to have libRadtran installed on your computer.
There is no Windows-native version of libRadtran, so that generally implies that you are running on a Unix/Linux,
however, reading and writing of uvspec input files, as well as reading of uvspec output files is possible on any
platform.

Important note concerning the uu output:
The uu field in the libRadtran/uvspec Case object contains the output radiances. In the most general case, this is a
5-dimensional numpy array, with axes in the following order:
1) umu - the cosine of the propagation zenith angles, where umu=1 is looking downward
2) phi - the propagation azimuth angle, where 180 deg is viewing northwards
3) spectral variable ('wavelength'/'wvl', 'wavenumber'/'wvn' and 'lambda' are alternative names)
4) altitudes of viewing point ('zout', 'zout_sea', 'pressure'/'p' and 'cth' are alternative level variables
5) stokes polarisation component (up to 4 stokes components, called I, Q, U and V)

By 'propagation' is meant the direction in which light is travelling, which is 180 degrees different from the
'viewing' direction.

Trailing singleton dimensions are dropped. That is, if there is only one stokes component, then uu has only 4
dimensions. If there is also only one level output (zout etc.), then uu has only 3 dimensions and so forth.

Leading and sandwiched singleton dimensions are not dropped in the current implementation. Dropping of singleton
dimensions can break existing code.

The relevant code here is taken from libRadtran version 2.0

"""

# TODO Reading of montecarlo (mystic/mc) output files, particularly:
# mc.flx.spc - spectral irradiance, actinic flux at levels
# mc.rad.spc - spectral radiance at levels
# TODO Dealing with exceptions, warnings. Keywords missing from the options library etc.
# TODO dealing with setting of units based on whatever is known about inputs/outputs, thermal is W/m^2/cm^-1


_isfloatnum = '^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$'  # regular expression for matching tokens to floating point

import writeLex  # This imports all the libradtran option definitions
import os
import easygui  # For file open dialogs
import numpy as np
import xray
import re
import warnings
from morticia.moglo import *
from morticia.tools.xd import *
import copy
from itertools import chain  #Used in RadEnv constructor

uvsOptions = writeLex.loadOptions()  # Load all options into a global dictionary of uvspec option specifications.

# The following dictionary provides units for some of the source solar files provided with libRadtran
sourceSolarUnits = {
    'apm_0_5nm': ['mW', 'm^2', 'nm'],
    'apm_1nm': ['mW', 'm^2', 'nm'],
    'atlas_plus_modtran': ['mW', 'm^2', 'nm'],
    'atlas_plus_modtran_ph': ['count/s', 'cm^2', 'nm'],  # count photons
    'atlas2': ['mW', 'm^2', 'nm'],
    'atlas3': ['mW', 'm^2', 'nm'],
    'fu': ['W', 'm^2', ''],  # in band
    'kato': ['W', 'm^2', ''],  # in band
    'kurudz_0.1nm.dat': ['mW', 'm^2', 'nm'],
    'kurudz_1.0nm.dat': ['mW', 'm^2', 'nm'],
    'NewGuey2003.dat': ['mW', 'm^2', 'nm'],
    'Thekaekara.dat': ['mW', 'm^2', 'nm']
}

# Units generally depend on the units in the solar flux file, but may be dictated by the correlated-k band model
uvspecOutVars = {
    'lambda': 'Wavelength [nm]',  # Cannot be used in Python because lambda is a keyword use 'wvl
    'wavelength': 'Wavelength [nm]', # scalar per line
    'wavenumber': 'Wavenumber [cm^1]',  # abbreviate to wvn
    'wvn': 'Wavenumber [cm^-1]',
    'wvl': 'Wavelength [nm]',
    'edir': 'Direct beam irradiance (same unit as extraterrestrial irradiance, e.g mW/m^2/nm if using the "atlas3" spectrum in the /data/solar_flux/ directory.)',
    'edn': 'Diffuse downwelling irradiance, i.e. total minus direct beam (same unit as edir).',
    'eup': 'Diffuse upwelling irradiance (same unit as edir).',
    'uavg': 'The mean intensity. Proportional to the actinic flux: To obtain the actinic flux, multiply the mean intensity by 4 pi (same unit as edir).',
    'uavgdir': 'Direct beam contribution to the mean intensity. (same unit as edir).',
    'uavgdn': 'Diffuse downward radiation contribution to the mean intensity. (same unit as edir).',
    'uavgup': 'Diffuse upward radiation contribution to the mean intensity. (same unit as edir).',
    'umu': 'Cosine of the zenith angles for sightline radiance (intensity) calculations.',  # This is a vector of values
    'u0u': 'The azimuthally averaged intensity at n_umu user specified angles umu. (units of e.g. mW/m^2/nm/sr if using the "atlas3" spectrum in the /data/solar_flux/ directory.)', # vector
    'uu': 'The radiance (intensity) at umu and phi user specified angles (unit e.g. mW/m^2/nm/sr if using the "atlas3" spectrum in the /data/solar_flux/ directory.)', # vector
    'uu_down': 'The downwelling radiances (intensity) at cmu and phi angles (unit e.g. mW/m^2/nm/sr if using the "atlas3" spectrum in the /data/solar_flux? directory.)', # vector
    'uu_up': 'The upwelling radiances (intensity) at cmu and phi angles (unit e.g. mW/m^2/nm/sr if using the "atlas3" spectrum in the /data/solar_flux/ directory.)', # vector
    'cmu': 'Computational polar angles from polradtran',
    'down_fluxI': 'The total (direct+diffuse) downward (down flux) irradiances (Stokes I component). Same units as extraterrestrial irradiance.',
    'up_fluxI': 'The total (direct+diffuse) upward (up flux) irradiances (Stokes I component). Same units as extraterrestrial irradiance.',
    'down_fluxQ': 'The total (direct+diffuse) downward (down flux) irradiances (Stokes Q component). Same units as extraterrestrial irradiance.',
    'up_fluxQ': 'The total (direct+diffuse) upward (up flux) irradiances (Stokes Q component). Same units as extraterrestrial irradiance.',
    'down_fluxU': 'The total (direct+diffuse) downward (down flux) irradiances (Stokes U component). Same units as extraterrestrial irradiance.',
    'up_fluxU': 'The total (direct+diffuse) upward (up flux) irradiances (Stokes U component). Same units as extraterrestrial irradiance',
    'down_fluxV': 'The total (direct+diffuse) downward (down flux) irradiances (Stokes V component). Same units as extraterrestrial irradiance.',
    'up_fluxV': 'The total (direct+diffuse) upward (up flux) irradiances (Stokes V component). Same units as extraterrestrial irradiance.',
    'eglo': 'Global downwelling irradiance',
    'enet': 'Global downwelling minus upwelling (net downward irradiance)',
    'esum': 'Global downwelling plus upwelling',
    'fdir': 'Direct actinic flux (scalar irradiance)',
    'fglo': 'Global actinic flux (scalar irradiance)',
    'fdn': 'Downwelling actinic flux (scalar irradiance)',
    'fup': 'Upwelling actinic flux (scalar irradiance)',
    'uavgglo': 'Total (global) mean diffuse intensity (radiance) = actinic flux/4pi',
    'f': 'Actinic flux (scalar irradiance)',
    'sza': 'Solar zenith angle [deg]',
    'n_air': 'Air refractive index',
    'zout': 'Altitude above ground [km]',
    'zout_sea': 'Altitude above sea level [km]',
    'sph_alb': 'Spherical albedo of the complete atmosphere',
    'albedo': 'Albedo',
    'heat': 'Heating rate in K/day',
    'n_xxx': 'Number density of gas xxx [cm^-3]',
    'rho_xxx': 'Mass density of gas xxx [kg/m^3]',
    'mmr_xxx': 'Mass mixing ratio of gas xxx [kg/kg]',
    'vmr_xxx': 'Volume mixing ratio of gas xxx [m^3/m^3]',
    'p': 'Pressure [hPa]',
    'T': 'Temperature [K]',
    'T_d': 'Dewpoint temperature [K]',
    'T_sur': 'Surface temperature [K]',
    'theta': 'Potential temperature [K]',
    'theta_e': 'Equivalent potential temperature [K]',
    'rh': 'Relative humidity over water [%]',
    'rh_ice': 'Relative humidity over ice [%]',
    'c_p': 'Specific heat capacity of the air',
    'CLWC': 'Cloud liquid water content [kg/kg]',
    'CLWD': 'Cloud liquid water density [g/m^3]',
    'CIWC': 'Cloud ice water content [kg/kg]',
    'CIWD': 'Cloud ice water density [g/m^3]',
    'TCC': 'Total cloud cover [0-1]',
    'cth': 'Cloud top height [km]'
}
#  the following gas species can appear for _xxx in the above list of output variables.
gasSpecies = ['air', 'O3', 'O2', 'H2O', 'CO2', 'NO2', 'BRO', 'OCLO', 'HCHO', 'O4']

# Insert the additional items programatically
outVars = uvspecOutVars.copy()  # first make a copy
for thisVar in uvspecOutVars:
    if thisVar[-4:] == '_xxx':
        for gas in gasSpecies:
            outVars[thisVar.replace('xxx', gas)] = outVars[thisVar].replace('xxx', gas)  # insert in copy
        del outVars[thisVar]  # delete the template entry
uvspecOutVars = outVars  # copy the updated dict back to the original

# For the output_user option, only certain variables are allowed as first and second index variables,
# These are wavelength (lambda, wvn), zout, zout_sea, p (pressure)
# The only two index variables that are allowed together is one height variable and one wavelength/number variable
# A height as well as a wavelength/number output should only be specified if there are both multiple
# wavelengths and multiple output heights. FOr the TZS solver, the cloud top height (cth) may be index.
indexVars = ['lambda', 'wavelength', 'wvl', 'wvn', 'zout', 'zout_sea', 'p', 'cth']

# Define a regexp for determining if a token of the zout keyword is a single level
# It is a single level if it is either a floating point number or the words boa, sur, cpt or toa (for surface,
# cold point tropopause and top of atmosphere)
re_isSingleOutputLevel = '(^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$)|(^boa$)|(^sur$)|(^surface$)|(^cpt$)|(^toa$)'

class Case():
    """ Class which encapsulates a run case of libRadtran/uvspec.
    This class has methods to read libRadtran/uvspec input files, write uvspec input files, run uvspec in parallel on
    multiple compute nodes and read uvspec output files. An important use-case is that of reading a uvspec input
    file called the "base case", altering the parameters of particular option keywords and then running the case
    and reading the outputs. This class is also used by the RadEnv class which encapsulates a radiant environment.
    Construction of radiant environment maps typically requires running an array of librad.Case instances.
    """
    # Definitions of some of the possible uvspec output variables

    def __init__(self, casename='', filename=None, optionlist=None):
        """ A libRadtran/uvspec case
        :param casename: A user-defined name for the libRadtran/uvspec case
        :param filename: An optional filename from which to read the libRadtran/uvspec input
        :param optionlist: A list of option keywords and parameteres (tokens). The keyword existence
         is verified. Besides that, no error checking is performed automatically (yet).
        :return:
        """
        self.name = casename
        self.error_txt = []
        self.options = []  # options is a list [option_name (string), option_tokens (list of strings),
        self.tokens = []  # option keyword parameters (tokens)
        self.optionobj = []  # option object from writeLex
        self.filorigin = []
        self.solver = 'disort'  # default, modified b y the rte_solver option keyword
        self.fluxline = ['wvl', 'edir', 'edn', 'eup', 'uavgdir', 'uavgdn', 'uavgup']  # default output
        self.wvl = []  # wavelengths, wavenumbers and output levels difficult to ascertain to start with
        self.wvn = []
        self.zout = np.zeros(1)  # If no zout (or equivalent) is given, uvspec produces output at the surface
        self.zout_sea = np.zeros(1)  # altitudes above seal level, same as zout unless ground 'altitude' provided
        self.pressure_out = []  # Only populated if pressure_out keyword is given
        self.output_user = ''  # set with the output_user keyword
        self.output_quantity = 'radiance' # default is radiances and irradiances in units determined by input solar file
        self.source = ''  # 'solar' or 'thermal'
        self.source_file = ''
        self.n_umu = 0  # number of zenith angles (actually cosine of zenith angle)
        self.umu = []  # zenith angles for radiance calculations
        self.n_phi = 0  # number of azimuth radiance angles
        self.phi = []  # azimuth angles for radiance calculations
        self.n_levels_out = 1  # Assume only one output level, unless zout/zout_sea/pressure_out keyword is used.
        self.levels_out = ['boa']  # Tokens provided on an output level keyword (zout, zout_sea, pressure_out), default
        self.levels_out_type = 'zout'  # Assume that type of levels out data is altitude above surface
        self.n_wvl = '?'  # number of wavelengths is difficult to ascertain to start with
        self.n_stokes = 1  # default number of stokes parameters for polradtran
        self.stokes = ['I']  # default is to compute intensity component only (all solvers, including polradtran)
        self.radND = []  # N-dimensional radiance data (umu, phi, wvn, zout, stokes)
        self.rad_units = 'radiance'  # radiance/irradiance units, could be K for brightness temperatures
        self.altitude = np.zeros(1)  # Default surface (ground) height above sea level
        if filename is not None:
            if not filename:
                # Open a dialog to get the filename
                filename = easygui.fileopenbox(msg='Please select a uvspec input file.', filetypes=["*.INP"])
            elif filename[-4:].lower() != '.inp':
                filename += '.INP'
            opdata, line_nos, path = Case.read(path=filename)
            self.infile = path
            self.outfile = self.infile[:-4] + '.OUT'
            # option_object, source_file_nos (filename, line_number)]
            # Process the results into lists of options
            for (optnumber, option) in enumerate(opdata):
                self.options.append(option[0])  # the option keyword (string)
                self.tokens.append(option[1:])  # The tokens following the keyword (list of strings)
                self.filorigin.append(line_nos[optnumber])  # The file origin of this keyword
                if self.options[optnumber] in uvsOptions:
                    self.optionobj.append(uvsOptions[self.options[optnumber]])  # The option object (dict lookup)
                else:
                    print('Warning, keyword option ' +  option[0] + ' not found in options library.')
                # Make any possible preparations for occurance of this keyword
                self.prepare_for_keyword(option[0],option[1:])
        if not self.name:  # set the name as the basname of the filename, excluding the extension
            self.name = os.path.basename(self.infile)[:-4]
        #TODO Build the case from the option list ?

    def prepare_for_solver(self, tokens):
        self.solver = tokens[0]  # First token gives the solver
        if any([self.solver == thesolver for thesolver in ['disort', 'disort2', 'sdisort',
                                                           'spsdisort', 'fdisort1', 'fdisort2']]):
            self.fluxline = ['wvl', 'edir', 'edn', 'eup', 'uavgdir', 'uavgdn', 'uavgup']  # default output
        elif any([self.solver == thesolver for thesolver in ['twostr','rodents']]):
            self.fluxline = ['wvl', 'edir', 'edn', 'eup', 'uavg']
        elif self.solver == 'polradtran':
            self.prepare_for_polradtran()
        elif self.solver == 'sslidar':
            self.fluxline = ['center_of_range', 'number_of_photons', 'lidar_ratio']
            self.irrad_units = ['count', '', '']

    def prepare_for_source(self, tokens):
        """ Prepare for source, particularly units of various kinds, depending on the source
        :param tokens: uvspec option parameters (tokens)
        :return:
        """

        # TODO : More work required here - see libRadtran manual under source keyword
        # TODO : Also need to verify default radiance output units (mW/m^2/sr/nm or W/m^2/sr/nm)
        if tokens[0] == 'solar':  # dealing with solar spectrum
            self.source = 'solar'
            if len(tokens) > 1:  # solar source file provided
                self.source_file = os.path.basename(tokens[1])  # extract the filename only
                # try to establish the radiometric units based on the filename
                if self.source_file in sourceSolarUnits:
                    self.rad_units = sourceSolarUnits[self.source_file]
                elif len(tokens) == 3:
                    self.rad_units = {'per_nm': ['W', 'm^2', 'nm'], 'per_cm-1': ['W', 'm^2', 'cm^-1'],
                                      'per_band': ['W', 'm^2', '']}[tokens[2]]
        elif tokens[0] == 'thermal':
            self.source = 'thermal'
            self.rad_units = ['W', 'm^2', 'cm^-1']

    def prepare_for_keyword(self, keyword, tokens):
        """ Make any possible preparations for occurrences of particular keywords
        :param keyword: The uvspec option keyword (string)
        :param tokens: The parameters (tokens) for the keyword as a list of strings
        :return:
        """
        # Prepare for different output formats, depending on the solver
        if keyword == 'rte_solver':
            self.prepare_for_solver(tokens)
        if keyword == 'source':
            self.prepare_for_source(tokens)
        # Prepare for radiances
        if keyword == 'umu':
            self.n_umu = len(tokens)  # The number of umu values
            self.umu = np.array(tokens).astype(np.float)
        if keyword == 'phi':
            self.n_phi = len(tokens)
            self.phi = np.array(tokens).astype(np.float)
        if keyword == 'output_user':
            self.output_user = [token.replace('lambda', 'wvl') for token in tokens]  # lambda is a keyword
            self.output_user = [token.replace('wavenumber', 'wvn') for token in self.output_user]  # abbreviate wavenumber
            self.output_user = [token.replace('wavelength', 'wvl') for token in self.output_user]  # abbreviate wavelength
            self.fluxline = []
        if keyword == 'polradtran' and tokens[0] == 'nstokes':
            self.n_stokes = int(tokens[1])
            if self.n_stokes == 1:
                self.stokes = ['I']
            elif self.n_stokes == 3:
                self.stokes = ['I', 'Q', 'U']
            elif self.n_stokes == 4:
                self.stokes = ['I', 'Q', 'U', 'V']
            else:
                raise ValueError('uvspec input polradtran nstokes must be 1, 3 or 4.')
            self.prepare_for_polradtran()
        if (keyword == 'zout' or keyword == 'zout_sea' or keyword == 'pressure_out' or
            keyword == 'tzs_cloud_top_height'):  # Determine number of output levels
            self.levels_out_type = keyword
            if all([re.match(re_isSingleOutputLevel, token.lower()) for token in tokens]):
                self.n_levels_out = len(tokens)
                self.levels_out = tokens
            else:  # Number of output levels is not known, will have to auto-detect
                self.n_levels_out = 0  # This indicates that the number of output levels is not known
        if keyword == 'output_quantity':  # Try to set output units
            self.output_quantity = tokens[0]
            if self.output_quantity == 'brightness':
                self.rad_units = ['K', '', '']
                self.irrad_units = ['K', '', '']
            elif self.output_quantity == 'reflectivity':
                self.rad_units = ['', '', '']
                self.irrad_units = ['', '', '']
            elif self.output_quantity == 'transmittance':
                self.rad_units = ['', '', '']
                self.irrad_units = ['', '', '']
        if keyword == 'heating_rate':
            self.output_user = ['zout', 'heat']
            self.fluxline = []  #TODO note that heating rate outputs for multiple wavelengths have a header line
        if keyword == 'print_disort_info':
            self.fluxline = '?'  # this output format is unknown or too complex to handle
        if keyword == 'altitude':
            self.altitude = np.float64(tokens[0])  # This is the ground altitude above sea level

    def prepare_for_polradtran(self):
        """ Prepare for output from the polradtran solver

        :return:
        """
        self.fluxline = ['wvl']
        for stokes in self.stokes:
            self.fluxline.extend(['down_flux' + stokes, 'up_flux' + stokes])

    def append_option(self, option, origin=('user', None)):
        """ Append a libRadtran/uvspec options to this uvspec case. It will be appended at the end of the file

        :param option: A list containing the keyword and keyword parameters (tokens)
        :param origin: A 2-tuple giving the origin of the option and a "line number" reference. Default ('user', None)
        uvspec options.
        :return:
        """
        self.options.append(option[0])  # the option keyword (string)
        self.tokens.append(option[1:])  # The tokens following the keyword (list of strings)
        self.filorigin.append(origin)  # The origin of this keyword
        self.optionobj.append(uvsOptions[option[0]])  # The option object
        # Make any possible preparations for occurance of this keyword
        self.prepare_for_keyword(option[0],option[1:])

    def alter_option(self, option, origin=('user', None)):
        """ Alter the parameters of a uvspec input option. If the option is not found, the option is appended with
        append_option instead.

        :param option: List of keyword and tokens (parameters) to provide to the option keyword (list of strings).
        :param origin: A 2-tuple noting the "origin" of the change to this keyword. Default ('user', None)
        :return:
        """
        try:
            ioption = self.options.index(option[0])
        except ValueError:
            self.append_option(option, origin)  # just append the option if not found
        else:
            self.tokens[ioption] = option[1:]  # The tokens following the keyword (list of strings)
            self.filorigin[ioption] = origin  # The origin of this keyword
            self.prepare_for_keyword(option[0], option[1:])

    def del_option(self, option, all=True):
        """ Delete a uvspec input option matching the given option.

        :param option: Keyword of option to be deleted
        :param all: A flag indicating if all matching options must be deleted or only the first occurrence. The
        default is to delete all matching occurrences.
        :return: True if an option was deleted or False if not
        """
        #TODO consider providing warning if options does not exist
        deletedsomething = False
        while option in self.options:
            deletedsomething = True
            ioption = self.options.index(option)
            self.options.pop(ioption)
            self.tokens.pop(ioption)
            self.filorigin.pop(ioption)
            self.optionobj.pop(ioption)
        if deletedsomething:
            # Run through all options and reconstruct preparations
            for (ioption, option) in enumerate(self.options):
                self.prepare_for_keyword(self.options[ioption], self.tokens[ioption])
        return deletedsomething

    @staticmethod
    def read(path, includes_seen=[]):
        """ Reads a libRadtran input file. This will construct the libRadtran case from the contents of the .INP file
        Adapted from code by libRadtran developers.

        :param path: File path from which to read the uvspec input
        :param includes_seen: List of files already included (for recursion purposes to avoid infinite include loops)
        :return: data, line_nos, path
          where data is the full data in the file with includes, line_nos shows the source of every line and
          path is the path to the main input file.
        """
        # Get the full path
        path = os.path.abspath(path)
        folder = os.path.dirname(path)
        # print includes_seen
        with open(path, 'rt') as INPfile:
            opdata = INPfile.readlines()  # read in the entire file and process in memory afterwards
        opdata = [line.split() for line in opdata]  # Split lines into keywords and option parameters (tokens)
        line_nos = [(path, i) for i in xrange(1, len(opdata)+1)]
        # Remove lines with comments and merge continuous lines
        line = 0
        while line < len(opdata):  # Remove empty lines, comments and merge options split over multiple file lines
            # Skip empty lines
            if not opdata[line]:
                opdata.pop(line)
                line_nos.pop(line)
                continue
            # Skip comments  #TODO save comments as well in the librad.Case
            if opdata[line][0].startswith("#"):
                opdata.pop(line)
                line_nos.pop(line)
                continue
            # Remove comments from the line  #TODO save comments into the librad.Case
            elif [True for word in opdata[line] if (word.find("#") != -1)]:
                tmp = []
                for word in opdata[line]:
                    pos = word.find("#")
                    if pos != -1:
                        if word != "#":
                            tmp.append(word[:pos])
                        break
                    else:
                        tmp.append(word)
                opdata[line] = tmp
            # continuous line
            elif opdata[line][-1].endswith("\\"):
                opdata[line][-1] = opdata[pos][-1][:-1] # remove the \
                    # if the \ was preceded and continued by whitespace
                if opdata[line][-1] == "":
                    opdata[line].pop()
                    line_nos.pop()
                opdata[line].extend(opdata[line + 1])
            else:
                line += 1
        # Get the includes and include them at the point where the include keyword appears
        all_opdata = []
        all_line_nos = []
        this_includes = includes_seen[:] # These are the include files seen up to this point in the recursion
        for line in xrange(len(opdata)):  # Run through all lines in the file and perform include substitutions
            if opdata[line][0] == 'include':  # Have found an include file, read it and append to all_opdata
                include_file = opdata[line][1]  # Obtain the filename
                include_file = os.path.normpath(os.path.join(folder, include_file))  # Extend to full path name
                if this_includes.count(include_file):  # This file has been included before
                    # print(this_includes, include_file)
                    raise IOError('Attempted to include the same uvspec input file more than once in parent.')
                this_includes.append(include_file)  # Add the filename to the list of included files already seen
                # Read the data from the included file
                inc_opdata, inc_line_nos, x = Case.read(include_file, this_includes)  # Recursion
                # Insert the data at this point in the file option and line number lists
                all_opdata.extend(inc_opdata)
                all_line_nos.extend(inc_line_nos)
            else:  # Not an include option, just append
                all_opdata.append(opdata[line])
                all_line_nos.append(line_nos[line])
        return all_opdata, all_line_nos, path

        # The following is the old code that inserted all include files at the end, which is not the same
        # behaviour as a C #INCLUDE statement, which inserts at the point where the #INCLUDE appears.
        # buff = 0
        # this_includes = []
        # for line in xrange(len(opdata)):
        #     if (opdata[line + buff][0] == "include" and len(opdata[line + buff]) == 2):
        #         include_file = opdata.pop(line + buff)[1]  # Obtain the filename of the included file and pop option
        #         include_file = os.path.normpath(os.path.join(folder, include_file))  # Extend to full path name
        #         this_includes.append(include_file)  # Add the filename to the list of included files at this level
        #         line_nos.pop(line + buff)  # Also pop the line numbers from the list of line numbers
        #         buff -= 1  # Take into account that a line has been removed
        # for include_path in this_includes:
        #     if not os.path.exists(include_path):
        #         msg = "Include file '%s' in '%s' does not exist." % (include_path, path)
        #         raise IOError(msg)
        #         #print " " * len(includes) + msg
        #     # If the file has been included before.
        #     elif (this_includes + includes_seen).count(include_path) != 1:  # Count number of times file is included
        #         msg = "File %s included more than once in %s. Please fix this." % (include_path, path)
        #         raise IOError(msg)
        #         #self.error_txt.append(msg)
        #         #print " " * len(includes) + msg
        #     else:
        #         include_data = Case.read(include_path, includes_seen + this_includes)
        #         # The include file might contain errors and return None.
        #         if include_data:
        #             opdata.extend(include_data[0])  #TODO surely an insert rather than extend ?
        #             line_nos.extend(include_data[1])
        # print opdata
        # print line_nos
        # return opdata, line_nos, path

    def __repr__(self):
        """ libRadtran/uvspec input data
        :return: The uvspec input data as it would appear in an input file.
        """
        uvsinp = []
        for (ioption, keyword) in enumerate(self.options):
            uvsinp.append(keyword + ' ' + ' '.join(self.tokens[ioption]))  #TODO add comments
        return '\n'.join(uvsinp)

    def write(self, filename=''):
        """ Write libRadtran/uvspec input to a file (.INP extension by default.
        If the filename input is given as '', a file save dialog will be presented

        :param filename: Filename to which to write the uvspec input
        :return:
        """
        if not filename:
            filename = easygui.filesavebox(msg='Please save the uvspec input file.', filetypes=["*.INP"])
        if filename[-4:].lower() != '.inp':
            filename += '.INP'
        with open(filename, 'wt') as uvINP:
            alldata = self.__repr__()
            uvINP.write(alldata)

    def distribute_flux_data(self, fluxdata):
        """ Distribute flux data read from uvspec output file to various fields

        :param fluxdata: Flux (irradiance) data read from uvspec output file
        :return:
        """
        #TODO rename this function to just distribute_data ?
        # First split the data amongst output levels or output wavelengths/wavenumbers
        # We assume and handle only those cases of output_user where the primary variable is
        # zout, lambda (wvl) or wavenumber (wvn)
        if self.output_user:  # distribute to user-defined output variables
            fields = self.output_user
        else:
            fields = self.fluxline
        datashape = fluxdata.shape
        if len(datashape) == 1:  # Need some special cases to deal with single line output files
            linecount = 1
        else:
            linecount = datashape[1]

        # Deal with the sahpe of the data output and try to reshape, depending on the number of
        # wavelengths and output levels (zout, zout_sea or pressure)
        #if len(datashape) == 2:
        #    if datashape[1] != len(fields):  # number of fields in data does not match number of fields
                # print datashape[1], ' not the same as ', len(fields) #TODO try to deal with this
        if (fields[0] == 'zout' or fields[0] == 'zout_sea' or fields[0] == 'p' or
            fields[0] == 'cth'):  # output level or pressure level is the primary variable
            if self.n_levels_out == 0:  # Unknown number of output levels
                # Try just using the number of unique values in the first column
                self.n_levels_out = len(np.unique(fluxdata[:,0]))
            fluxdata = fluxdata.reshape((self.n_levels_out, -1, linecount), order='F')
        elif fields[0] == 'wvl' or fields[0] == 'wvn':  # wavelength/wavenumber is the primary variable
            if self.n_levels_out == 0:  # Don't know number of output levels
                self.n_wvl = len(np.unique(fluxdata[:,0]))  # Try to determine number of wavelengths/wavenumbers
                fluxdata = fluxdata.reshape((self.n_wvl, -1, linecount), order='F')
            else:
                fluxdata = fluxdata.reshape((-1, self.n_levels_out, linecount), order='F')
        else:  # Assume secondary variable is zout
            fluxdata = fluxdata.reshape((-1, self.n_levels_out, linecount), order='F')  #TODO provide warning or something
        self.fluxdata = fluxdata  # retain the flux data in the instance, reshaped as well as possible
        if linecount == 1:  # here the data is actually distributed, single line a special case
            # Some output fields, such as umu, uu, u0u, uu_down, uu_up, cmu(?) are vectors and therefore occupy
            # multiple columns, so keep track of columns and try to
            colstart = 0  # keep track of starting column
            for (ifield, field) in enumerate(fields):
                if field == 'uu' or field == 'uu_up' or field == 'uu_down':  # Do uu_up and uu_down actually exist ?
                    # Number of columns is n_umu * n_phi
                    ncols = max(self.n_umu, 1) * max(self.n_phi, 1)
                elif field == 'u0u':  #TODO does this exist and if so, what is the size ?
                    pass
                elif field == 'umu':
                    ncols = self.n_umu
                else:  # All other output variables are assumed to occupy only one column
                    ncols = 1
                setattr(self, field, np.squeeze(fluxdata[colstart:(colstart + ncols)]))
                colstart = colstart + ncols
        else:
            # Some output fields, such as umu, uu, u0u, uu_down, uu_up, cmu(?) are vectors and therefore occupy
            # multiple columns, so keep track of columns and try to
            colstart = 0  # keep track of starting column
            for (ifield, field) in enumerate(fields):
                if field == 'uu' or field == 'uu_up' or field == 'uu_down':  # Do uu_up and uu_down actually exist ?
                    # Number of columns is n_umu * n_phi
                    ncols = max(self.n_umu, 1) * max(self.n_phi, 1)
                elif field == 'u0u':  #TODO does this exist and if so, what is the size ?
                    pass
                elif field == 'umu':
                    ncols = self.n_umu
                else:  # All other output variables are assumed to occupy only one column
                    ncols = 1
                setattr(self, field, np.squeeze(fluxdata[:, :, colstart:(colstart + ncols)]))
                colstart = colstart + ncols
        # Clean up zout and wavelength/wavenumber data
        # Convert levels to real numbers
        level_dict = {'boa': self.altitude, 'BOA': self.altitude, 'sur': self.altitude,
                      'SUR': self.altitude, 'surface': self.altitude, 'SURFACE': self.altitude,
                      'toa': np.inf, 'TOA': np.inf, 'cpt': np.nan, 'CPT': np.nan}
        level_values = np.array([])
        for level in self.levels_out:
            try:
                level_values = np.append(level_values, np.float64(level))
            except ValueError:
                if level in level_dict:
                    level_values = np.append(level_values, level_dict[level])  # Try translating
                else:
                    warnings.warn('Invalid level token found in ' + self.levels_out_type + ' keyword.')
        #print 'level_values ++'
        #print level_values
        self.level_values = level_values
        #self.zout = np.unique(self.zout)  #TODO check that this does not reorder the zout values (especially pressure)
        #if len(self.zout) > 0:  #  TODO : Problems here with pressure_out, zout_sea etc.
        if self.levels_out_type == 'zout':
            self.zout = np.unique(self.level_values)
            self.n_levels_out = len(self.zout)
            self.zout_sea = self.zout + self.altitude
        elif self.levels_out_type == 'zout_sea':
            self.zout_sea = np.unique(self.level_values)
            self.n_levels_out = len(self.zout_sea)
            self.zout = self.zout_sea - self.altitude
        elif self.levels_out_type == 'pressure_out':
            self.pressure_out = np.unique(self.level_values)[::-1]  # Reverse order
            self.n_levels_out = len(self.pressure_out)
            self.zout = np.nan
            self.zout_sea = np.nan

        #    self.n_levels_out = len(self.zout)
        self.wvn = np.unique(self.wvn)
        self.wvl = np.unique(self.wvl)
        if len(self.wvl) > 0 and len(self.wvn) == 0:  # Calculate wavenumbers if wavelengths available
            self.wvn = 1e7 / self.wvl
        if len(self.wvn) > 0 and len(self.wvl) == 0:  # Calculate wavelengths if wavenumbers available
            self.wvl = 1e7 / self.wvn
        self.n_wvl = len(self.wvl)


    def readout(self, filename=None):
        """ Read uvspec output and assign to variables as intelligently as possible.

         The general process of reading is:
          1) If the user has specified output_user, just assume a flat file and read using
             np.loadtxt or np.genfromtxt.
          2) If not user_output and the solver has no radiance blocks, assume a flat file and
             read using np.loadtxt. The variables to be read should be contained in the self.fluxline attribute.
          3) Otherwise, if the output has radiance blocks, read those depending on the radiance block
             format for the specific solver. Keep reading flux and radiance blocks until the file is exhausted.

         Once the data has all been read, the data is split up between the number of output levels and number of
         wavelengths. For radiance data, the order of numpy dimensions is *umu*, *phi*, *wavelength*, *zout* and *stokes*. That
         is, if a case has multiple zenith angles, multiple azimuth angles, multiple wavelengths and multiple output
         levels, the radiance property uu will have 4 dimensions. In the case of polradtran, there will be 5
         dimensions to include the stokes parameters (if more than 1, which is the I = intensity parameter).

        Output from uvspec depends on the solver and a number of other inputs, including the directive ``output_user`.
        For the solvers ``disort``, ``sdisort``, ``spsdisort`` and presumably also ``disort2``, the irradiance (flux) outputs default
        to
        ::
         lambda edir edn eup uavgdir uavgdn uavgup

        If radiances (intensities) have been requested with the umu
        (cosine zenith angles input), each line of flux data is followed
        by a block of radiance data as follows:
        ::
         umu(0) u0u(umu(0))
         umu(1) u0u(umu(1))
         . . . .
         . . . .
         umu(n) u0u(umu(n))

        where u0u is the azimuthally averaged radiance for the requested zenith
        angles.

        If azimuth angles (phi) have also been specified, then the
        radiance block is extended as follows:
        ::
                                phi(0)        ...     phi(m)
         umu(0) u0u(umu(0)) uu(umu(0),phi(0)) ... uu(umu(0),phi(m))
         umu(1) u0u(umu(1)) uu(umu(1),phi(0)) ... uu(umu(1),phi(m))
         . . . .
         . . . .
         umu(n) u0u(umu(n)) uu(umu(n),phi(0)) ... uu(umu(n),phi(m))

        Radiance outputs are not affected by output_user options.

        For the polradtran solver, the flux block is as follows:
        ::
           lambda down_flux(1) up_flux(1) ... down_flux(iS) up_flux(iS)

        where iS is the number of Stokes parameters specified using the
        'polradtran nstokes' directive.
        If umu and phi are also specified, the radiance block is as
        follows:
        ::
                                 phi(0)      ...      phi(m)
         Stokes vector I
         umu(0) u0u(umu(0)) uu(umu(0),phi(0)) ... uu(umu(0),phi(m))
         umu(1) u0u(umu(1)) uu(umu(1),phi(0)) ... uu(umu(1),phi(m))
         . . . .
         . . . .
         umu(n) u0u(umu(n)) uu(umu(n),phi(0)) ... uu(umu(n),phi(m))
         Stokes vector Q
         . . .
         . . .

        The u0u (azimuthally averaged radiance) is always zero for
        polradtran.

        For the two-stream solver (twostr), the flux block is
        ::
           lambda edir edn eup uavg

        The directive keyword ``brightness`` can also change output. The documentation simply states that radiances and
        irradiances are just converted to brightness temperatures.

        The keyword directive ``zout`` and it's parameters will influence output format as well. In general the output
        is repeated for each given value of ``zout`` or ``zout_sea``.

        The keyword directive ``output`` and its parameters will also have a major effect.

        The ``output sum`` keyword sums output data over the wavelength dimension. This in contrast to ``output integrate``,
        which performs a spectral integral.

        The keyword directive ``header`` should not be used at all. This produces some header information in the output
        that will cause errors. A warning is issued of the ``header`` keyword is used in the input.

        :param filename: File from which to read the output. Defaults to name of input file, but with the .OUT
        extension.
        :return:

        """
        #TODO check for use of header keyword
        if self.fluxline == '?':
            print('Unknown output format. Skipping file read.')
            return
        if filename is None:
            filename = self.outfile
        elif filename == '':
            filename = easygui.fileopenbox(msg='Please select the uvspec output file.', filetypes=["*.OUT"])
        fluxdata = []
        if not os.path.isfile(filename):
            print('Output file does not exist. Run uvspec.')
            return
        if self.output_user:
            fluxdata = np.loadtxt(filename)
            self.distribute_flux_data(fluxdata)
        elif (self.n_phi == 0 and self.n_umu == 0) or self.solver == 'sslidar':  # There are no radiance blocks
            fluxdata = np.loadtxt(filename)
            self.distribute_flux_data(fluxdata)
        # elif self.n_phi == 0:   # Not sure about format for n_umu > 0, n_phi == 0
        #  Look at example UVSPEC_FILTER_SOLAR.INP, which indicates manual is not correct
        #     fluxdata = np.loadtext(filename)
        #     # Take away the radiance data
        #     raddata = fluxdata[len(self.fluxline):]
        #     fluxdata = fluxdata[:len(self.fluxline)]
        #     self.raddata = raddata
        #     self.fluxdata = fluxdata
        #     self.distribute_flux_data(fluxdata)
        else:  # radiance blocks  #TODO polradtran has different format
            phicheck = []
            radND = []  # full 3D/4D radiance data is here (umu, phi and wavelength)
            with open(filename, 'rt') as uvOUT:
                # Read and append a line of flux data
                txtline = uvOUT.readline()
                while txtline:
                    fluxline = np.array(txtline.split()).astype(np.float)
                    if len(fluxdata) > 0:
                        fluxdata = np.vstack((fluxdata, fluxline))
                    else:
                        fluxdata = fluxline
                    # Read the radiance block
                    if self.n_phi > 0:  # There is a line of phi angles
                        philine = uvOUT.readline()  #TODO check that phi angles are correct
                        phicheck = np.array(philine.split()).astype(np.float)
                    # Read the lines for the umu values (radiances in radiance block)
                    raddata = []  # All polarisation blocks are vstacked
                    for polComp in self.stokes:  # read a radiances block for each polarisation component
                        if self.solver == 'polradtran':  # Read the polarisation block header
                            polBlockHeader = uvOUT.readline().strip()
                            # print polBlockHeader + '}'
                            if not re.match('^Stokes vector ' + polComp + '$', polBlockHeader):
                                raise IOError('Stokes vector header not found for component ' + polComp)
                        # raddata = []
                        for i_umuline in range(self.n_umu):  #TODO this is possibly wrong if there is no phi specified - see manual
                            umuline = uvOUT.readline()
                            radline = np.array(umuline.split()).astype(np.float)
                            if len(raddata) == 0:
                                raddata = radline
                            else:
                                raddata = np.vstack((raddata, radline))
                    if len(radND) == 0:
                        radND = raddata
                    else:
                        radND = np.dstack((radND, raddata))
                    txtline = uvOUT.readline()  # Read what should be the next line of flux data

            self.distribute_flux_data(fluxdata)  # distribute the flux data, which should also determine
                                                 # the number of wavelengths definitively
            self.radND = radND
            self.phi_check = phicheck

            # See if one size fits all
            self.u0u = radND[:,1].reshape(self.n_umu, self.n_stokes, self.n_wvl, -1, order='F').squeeze()  # should actually all be zero
            self.uu = radND[:,2:]
            self.uu = self.uu.reshape((self.n_umu, self.n_stokes, max(self.n_phi, 1), self.n_wvl, -1), order='F')
            self.uu = self.uu.transpose([0, 2, 3, 4, 1])  # transpose so that the nstokes axis is last
            # At this point, uu should be 5 dimensional (possibly with singleton dimensions) in order of
            # 'umu', 'phi', 'wv', 'zout' (or equivalent)
            self.process_outputs()
            # while self.uu.shape[-1] == 1:  # Remove any hanging singleton dimensions at the end
            #    self.uu = self.uu.squeeze(axis=self.uu.ndim-1)

            # if self.solver == 'polradtran':
            #     self.u0u = radND[:,1].reshape(self.n_umu, self.nstokes, self.n_wvl, -1, order='F').squeeze()  # should actually all be zero
            #     if radND.ndim == 3:  # There are multiple n_phi values
            #         self.uu = radND[:,2:,:]
            #         # Reshape to multiple nstokes values, multiple wavelengths and multiple zout levels
            #         self.uu = self.uu.reshape((self.n_umu, self.nstokes, self.n_phi, self.n_wvl, -1), order='F').squeeze()
            #
            #     else: # There are only different umu values, no different phi values
            #         self.uu = radND[:,2:]
            #         # reshape to multiple nstokes
            #         self.uu = self.uu.reshape((self.n_umu, self.nstokes, max(self.n_phi, 1), self.n_wvl, -1), order='F').squeeze()
            #
            # else:
            #     self.u0u = radND[:,1].reshape(self.n_umu, self.nstokes, self.n_wvl, -1, order='F').squeeze()
            #     #if self.u0u.shape[1] / self.n_wvl > 1:  #TODO problem here with single wavelength data
            #     #    self.u0u = self.u0u.reshape((self.n_umu, self.n_wvl, -1), order='F')
            #     if radND.ndim == 3:
            #         self.uu = radND[:,2:,:]
            #         # If there are multiple zout levels, must reshape self.uu
            #         if self.uu.shape[2] / self.n_wvl > 1:
            #             self.uu = self.uu.reshape((self.n_umu, self.n_phi, self.n_wvl, -1), order='F')
            #     else:
            #         self.uu = radND[:,2:]
            #         if self.uu.shape[1] / self.n_wvl > 1:  # if multiple output levels, reshape the radiance data appropriately
            #             self.uu = self.uu.reshape((self.n_umu, self.n_wvl, -1), order='F')

    def run(self, stderr_to_file=False, write_input=True, read_output=True, block=True):
        """ Run the libRadtran/uvspec case.

        This will run the libRadtran/uvspec Case instance provided. Some control is provided regarding the handling of
        the standard error output from uvspec. This function only returns when uvspec terminates.

        :param stderr_to_file: Controls whether standard error output goes to the screen or is written to a file
            having the same name as the input/output files, except with the extension .ERR.
        :param write_input: Controls whether the input file is writte out before execution. Default is True.
        :param read_output: Controls whether the output file is read after execution. Default is True.
        :param block: By default, this method waits until uvspec terminates. If set False, the uvspec process
            is released to background and read_output is set to False (regardless of user input).
        :return: The shell command string executed in order to run the case and the return status of the command.
        """
        # Write input file by default
        import subprocess
        if write_input:
            self.write(filename=self.name+'.INP')
        # Spawn a sub-process using the subprocess module
        if not stderr_to_file:
            try:
                with open(self.name+'.INP', 'rt') as stin, \
                     open(self.name+'.OUT', 'wt') as stout:
                    return_code = subprocess.call(['uvspec'], stdin=stin, stdout=stout)
            except OSError:  # the uvspec command likely does not exist
                warnings.warn('Unable to spawn uvspec process. Probably not installed system-wide on platform.')
                return_code = 1
        else:  # redirect the standard error output to a file as well
            try:
                with open(self.name+'.INP', 'rt') as stin, \
                     open(self.name+'.OUT', 'wt') as stout, \
                     open(self.name+'.ERR', 'wt') as sterr:
                    return_code = subprocess.call(['uvspec'], stdin=stin, stdout=stout, sterr=sterr)
            except OSError:  # the uvspec command likely does not exist
                warnings.warn('Unable to spawn uvspec process. Probably not installed system-wide on platform.')
                return_code = 1
        if not return_code and read_output:
            self.readout(filename=self.name+'.OUT')  # Read the output into the instance if the return code OK
        return self

    def process_outputs(self):
        """ Process outputs from libRadtran into moglo.Scalar and xray.DataArray objects.

        Note that this method probably does not cover all libRadtran/uvspec inputs and outputs and will most
        likely continue to evolve, perhaps breaking existing code.
        :return:
        """
        # Determine output levels if possible
        # 'cpt', being cold-point tropopause is mapped to np.nan and toa maps to np.inf


        # The most important output for MORTICIA is radiances (self.uu), so those get processed
        # first. This is a 5 dimensional numpy array with axes 'umu'. 'phi', 'wvl', 'zout' (or equivalent)
        # and 'stokes'.
        # Create each of the axes individually using xd_identity
        if len(self.uu):  # OK, there is some radiance data
            uu_shape = self.uu.shape
            umu = xd_identity(self.umu, 'umu', '')
            if len(self.phi):
                pass
            else:
                self.phi = np.zeros(1)
            phi = xd_identity(self.phi, 'phi', 'deg')
            wvl = xd_identity(self.wvl, 'wvl', 'nm')
            wvn = xd_identity(self.wvn, 'wvn', 'cm^-1')
            levels = xd_identity(self.level_values, self.levels_out_type)  # Presume units correct
            stokes = xd_identity(range(self.n_stokes), 'stokes')
            # Determine the units of uu
            if self.output_quantity == 'radiance':
                uu_units = self.rad_units[0] + '/' + self.rad_units[1] + '/sr'
                if self.rad_units[2]:
                    uu_units += '/' + self.rad_units[2]
            elif self.output_quantity == 'brightness':
                uu_units = 'K'
            elif self.output_quantity == 'transmittance' or self.levels_out == 'reflectivity':
                uu_units = ''
            # print 'units : ' + uu_units
            # Build the xray.DataArray
            qty_name = {'radiance': 'specrad', 'transmittance': 'trnx', 'reflectivity': 'reflx'}[self.output_quantity]
            uu = xray.DataArray(self.uu, [umu, phi, wvl, levels, stokes],
                                name=qty_name, attrs={'units': uu_units})
            self.xd_uu = uu


class RadEnv():
    """ RadEnv is a class to encapsulate a large number of uvspec runs to cover a large number of sightlines over the
    whole sphere. A radiance map over the complete sphere is called a radiant environment map. The uvspec utility can
    only handle a limited number of sighlines per run, determined by the maximum number of polar and azimuthal angles
    specified in the file /libsrc_f/DISORT.MXD. If these values are changed, DISORT and uvspec must be recompiled. If
    the values are set too large, the memory requirements could easily exceed your computer's limit (there is
    currently no dynamic memory allocation in DISORT). The situation for the cdisort solver is less clear.

    """

    def __init__(self, base_case, n_pol, n_azi, mxumu=48, mxphi=19, hemi=False):
        """ Create a set of uvspec runs covering the whole sphere to calculate a full radiant environment map.
        Where the base_case is the uvspec case on which to base the environmental map, Name is the name to give the
        environmental map and n_pol and n_azi are the number of polar and azimuthal sightline angles to generate. The
        mxumu and mxphi are the maximum number of polar and azimuth angles to calculate in a single run of uvspec.
        The default values are mxumu = 48, and mxphi = 19. These values are taken from the standard libRadtran
        distribution (/libsrc_f/DISORT.MXD) maximum parameter file. If using the polradtran solver, the corresponding
        file is /libsrc_f/POLRADTRAN.MXD. Other solvers may have different restrictions. A warning will be issued if
        the solver is not in the DISORT/POLRADTRAN family.

        :param base_case: librad.Case object providing the case on which the environment map is to be based
        :param n_pol: Number of polar angles (view/propagation zenith angles)
        :param n_azi: Number of azimuthal angles
        :param mxumu: Maximum number of polar angles per case
        :param mxphi: Maximum number of azimuthal angles per case
        :param hemi: If set True, will generate only a single hemisphere being on one side of
            the solar principle plane. Default is False i.e. the environment map covers the full sphere

        The solver cdisort may have dynamic memory allocation, so the warning is still issued because the situation
        is less clear.

        A note about radiative propagation angles and viewing angles, which are 180 deg opposite to each other.
        For radiance calculations define the cosine of the viewing zenith angle
        `umu` and the sensor azimuth `phi` and don't forget to also specify the solar azimuth
        `phi0`. `umu` > 0 means sensor looking downward (e.g. a satellite), `umu` < 0 means looking
        upward. `phi` = `phi0` indicates that the sensor looks into the direction of the sun,
        `phi` - `phi0` = 180 means that the sun is in the back of the sensor.

        The value of `umu` is the cosine of the propagation zenith angle. The value of `phi` is the true azimuth of
        the propagation direction.

        For all one-dimensional solvers the absolute azimuth does not matter, but only the relative azimuth
        `phi` - `phi0`.
        """
        # Require that n_pol be even
        # This is to ensure that there is no umu = 0 (horizontal direction, illegal in libRAdtran)
        if n_pol % 2:  # check comment below about Matlab polar angles
            warnings.warn('Input n_pol to librad.RadEnv must be even. Increased by 1.')
            n_pol += 1
        view_zen_angles = np.linspace(0.0, 180.0, n_pol)  # Viewing straight down is view zenith angle of 180 deg
        # The following line comes from Matlab - why only one half ?
        # TODO : Resolve question as to why only one half of angles in polar-angles
        # TODO : Also look at linspace for azimuth angles - want to avoid duplication (wrap)
        # One option is to add one point at 360 and then delete it.
        # another option is to avoid wrap by
        prop_zen_angles = np.linspace(-np.pi, 0.0, n_pol)  # Radiation travelling straight up is propagation zenith angle of 0
        view_zen_angles = np.linspace(0.0, 180.0, n_pol)
        umu = np.cos(prop_zen_angles)  # Negative umu is upward-looking, downwards propagating
        # TODO : Is it necessary to have the complete sphere
        # TODO : is it not sufficient to have a solar prinicple plane hemisphere ?
        if hemi:
            prop_azi_angles = np.linspace(0.0, 180.0, n_azi + 1)[0:-1]  # TODO : This has a wrap problem ?
            view_azi_angles = np.linspace(-180.0, 0.0, n_azi + 1)[0:-1]
        else:
            prop_azi_angles = np.linspace(0.0, 360.0, n_azi + 1)[0:-1]
            view_azi_angles = np.linspace(-180.0, 180.0,  n_azi + 1)[0:-1]

        #
        phi = prop_azi_angles
        # umu = xd_identity(umu, 'umu')
        # Calculate number of batches in azimuthal and polar anlges
        n_azi_batch = np.int(np.ceil(np.float(len(prop_azi_angles))/mxphi))
        n_pol_batch = np.int(np.ceil(np.float(len(prop_zen_angles))/mxumu))
        # Create an list of lists with all these batches of librad.Case
        self.cases = [[copy.deepcopy(base_case) for i_azi in range(n_azi_batch)] for j_pol in range(n_pol_batch)]

        # TODO : Take care of phi0 input in the case of hemi=True
        for iazi, iazi_start in enumerate(range(0, len(phi), mxphi)):
            batch_azi = phi[iazi_start:np.minimum(iazi_start+mxphi, len(phi))]
            for ipol, ipol_start in enumerate(range(0, len(umu), mxumu)):
                batch_pol = umu[ipol_start:np.minimum(ipol_start+mxumu, len(umu))]
                # Set the umu and phi keyword parameters
                self.cases[ipol][iazi].alter_option(['phi'] + [str(x) for x in batch_azi])
                #print iazi, ipol,
                #print len([str(x) for x in batch_azi]),
                #print len([str(x) for x in batch_pol])
                #print ['azi'] + [str(x) for x in batch_pol]
                self.cases[ipol][iazi].alter_option(['umu'] + [str(x) for x in batch_pol])
                self.cases[ipol][iazi].infile = (self.cases[ipol][iazi].infile[:-4] +
                                                 '_{:04d}_{:04d}.INP'.format(ipol, iazi))
                self.cases[ipol][iazi].outfile = (self.cases[ipol][iazi].outfile[:-4] +
                                                 '_{:04d}_{:04d}.OUT'.format(ipol, iazi))
                self.cases[ipol][iazi].name = (self.cases[ipol][iazi].name[:-4] +
                                                 '_{:04d}_{:04d}'.format(ipol, iazi))
                if hemi:  # Doing only one hemisphere along solar principal plane
                    self.cases[ipol][iazi].alter_option(['phi0', '0.0'])  # Sun shining towards North
        # Create a flattened list view of the cases
        # Put all the cases into a single list
        self.casechain = list(chain(*self.cases))  # This creates a linear view of the cases
        self.hemi = hemi
        self.n_azi = n_azi
        self.n_pol = n_pol
        self.n_azi_batch = n_azi_batch
        self.n_pol_batch = n_pol_batch
        self.phi = xd_identity(prop_azi_angles, 'phi')
        self.umu = xd_identity(umu, 'umu')
        self.pza = xd_identity(np.linspace(-180.0, 0.0, n_pol), 'pza')
        self.vza = xd_identity(view_zen_angles, 'vza')
        self.vaz = xd_identity(view_azi_angles, 'vaz')
        self.uu = np.array([])

    def run_ipyparallel(self, ipyparallel_view, stderr_to_file=False):
        """ Run a complete set of radiant environment map cases of libRadtran/uvspec using the `ipyparallel`
        Python package, which provides parallel computation from Jupyter notebooks and other Python launch
        modes.

        :param ipyparallel_view: an ipyparallel view of a Python engine cluster (see ipyparallel documentation.)
            Typical code for setting up the view:

            >>> from ipyparallel import Client
            >>> paraclient = Client(profile='mycluster', sshserver='me@mycluster.info', password='mypassword')
            >>> paraclient[:].use_dill()  # Need dill as a pickle replacement for our purposes here
            >>> ipyparallel_view = paraclient.load_balanced_view()
            >>> ipyparallel_view.block = True  # Must wait for completion of all tasks on the cluster

        :param stderr_to_file: If set to True, standard error output will be sent to a file. use only for debugging
            purposes.
        :return:
        """
        # The following should work, but the list casechain is completely reassigned
        # instead of being assigned element for element
        self.casechain = ipyparallel_view.map(Case.run, self.casechain)
        # Now recreate the list of lists view
        self.cases = [[self.casechain[i_pol * self.n_azi_batch + i_azi] for i_azi in range(self.n_azi_batch)]
                                                                        for i_pol in range(self.n_pol_batch)]
        # Compile the radiance results into one large array
        self.uu = np.hstack([np.vstack([self.cases[i_pol][i_azi].uu for i_pol in range(self.n_pol_batch)])
                                                                    for i_azi in range(self.n_azi_batch)])
        # Delete the individual results in an attempt to save memory
        for case in self.casechain:
            del case.uu

    def run_parallel(self, n_nodes=4):
        """ Run the RadEnv in multiprocessing mode on the local host.

        This is not yet tested, but should work with the multiprocessing package on the local host to use all
        the available cores. Will only work if libRadtran is installed on the local host.

        In order to use dill instead of pickle, it is necessary to use the pathos multiprocessing module
        instead of the standard multiprocessing module

        :param n_nodes: Number of compute nodes to use. Default is 4. Preferably set to number of cores you have
            available on the local host.
        :return:
        """
        from pathos.multiprocessing import ProcessingPool
        worker_pool = ProcessingPool(nodes=n_nodes)
        self.casechain = worker_pool.map(Case.run, self.casechain)
        # Now recreate the list of lists view
        self.cases = [[self.casechain[i_pol * self.n_azi_batch + i_azi] for i_azi in range(self.n_azi_batch)]
                                                                        for i_pol in range(self.n_pol_batch)]
        # Compile the radiance results into one large array
        self.uu = np.hstack([np.vstack([self.cases[i_pol][i_azi].uu for i_pol in range(self.n_pol_batch)])
                                                                    for i_azi in range(self.n_azi_batch)])
        # Delete the individual results in an attempt to save memory
        for case in self.casechain:
            del case.uu








